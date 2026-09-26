#!/usr/bin/env python3
"""QLoRA supervised fine-tuning of an open-weight model on engine-verified godotai runs.

Input : the JSONL written by ``python3 -m godotai dataset extract`` (one trajectory per
        line: system prompt, tool definitions, user/assistant/tool turns — every run in it
        passed the real Godot {pin} verification pipeline).
Output: a PEFT LoRA adapter (+ optionally the merged model) that the agent can serve
        with any OpenAI-compatible server (vLLM, llama.cpp, Ollama) via
        ``GODOTAI_PROVIDER=openai_compat``.

    # 1. offline, stdlib only — validates the dataset and prints token statistics
    python3 training/train_qlora.py --data data/sft.jsonl --dry-run

    # 2. on a GPU box (Kaggle "GPU T4 x2", a rented A100, …)
    pip install "torch" "transformers>=4.57" "peft>=0.17" "trl>=1.14" "datasets" "bitsandbytes" "accelerate"
    python3 training/train_qlora.py --data data/sft.jsonl --model Qwen/Qwen2.5-Coder-7B-Instruct \\
        --out training/output/godotai-qwen7b-lora --max-seq-length 4096 --epochs 2

STATUS (2026-09-26): the ``--dry-run`` path is unit-tested; the training path is written
against the TRL v1.14 / transformers documentation read on 2026-09-26 (``SFTConfig.max_length``,
``assistant_only_loss``, ``processing_class``, ``peft_config``; ``from_pretrained(dtype=...)``
with ``torch_dtype`` deprecated) but has **not been executed** in the environment that
produced this repository (no GPU there). Treat the first real run as an experiment and
judge the result only with ``python3 -m godotai eval run`` — the engine, not the loss
curve, decides whether the model got better at Godot.

Base-model notes (checked 2026-09-26, Hugging Face model cards)
* ``Qwen/Qwen2.5-Coder-7B-Instruct`` (default): dense, plain transformer, tool-calling chat
  template, widely used with bitsandbytes/PEFT → the low-risk choice for 2 × T4 (16 GB).
* ``Qwen/Qwen3.5-9B``: 9 B, 262 k native context, thinking mode by default (``<think>``),
  strong tool calling per its card — but it is a *vision-language* model with a hybrid
  (Gated DeltaNet + MoE) architecture and requires the latest transformers; 4-bit
  bitsandbytes + PEFT support for that architecture is an **assumption we did not verify**.
* ``Qwen/Qwen3.6-27B`` (April 2026, dense coder): too large for 16 GB cards in a training
  configuration; needs ≥ 24 GB per GPU or multi-GPU sharding.

Design notes
* Tool calls are rendered with the *model's own* chat template
  (``tokenizer.apply_chat_template(messages, tools=...)``); models whose template has no
  tool support (rare among 2025+ coder models) fall back to a plain JSON rendering.
* 4-bit NF4 base weights + LoRA on all linear projections (the standard QLoRA recipe).
  On T4 GPUs there is no bfloat16 → fp16 compute; on A100/L4/H100 bf16 is used.
* Kaggle sessions are capped at 12 h (GPU) — keep ``--epochs`` small, save every
  ``--save-steps`` and put the output under ``/kaggle/working`` (20 GB, persisted).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

REQUIRED_TOP = ("messages",)
ROLES = {"user", "assistant", "tool"}


class DatasetError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Dataset loading / validation (stdlib)
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path}:{n}: invalid JSON ({exc})") from None


def validate_example(ex: dict[str, Any], where: str = "") -> list[str]:
    """Return a list of problems (empty when the example is usable)."""
    problems: list[str] = []
    for k in REQUIRED_TOP:
        if k not in ex:
            problems.append(f"{where}missing {k!r}")
    msgs = ex.get("messages") or []
    if not msgs:
        problems.append(f"{where}no messages")
        return problems
    if msgs[0].get("role") != "user":
        problems.append(f"{where}first message must be from the user")
    if not any(m.get("role") == "assistant" for m in msgs):
        problems.append(f"{where}no assistant turn")
    pending: set[str] = set()
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role not in ROLES:
            problems.append(f"{where}message {i}: bad role {role!r}")
            continue
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                if not tc.get("name"):
                    problems.append(f"{where}message {i}: tool call without name")
                if not isinstance(tc.get("args", {}), dict):
                    problems.append(f"{where}message {i}: tool call args must be an object")
                if tc.get("id"):
                    pending.add(tc["id"])
            if not (m.get("content") or m.get("tool_calls")):
                problems.append(f"{where}message {i}: empty assistant turn")
        elif role == "tool":
            tid = m.get("tool_call_id")
            if tid and tid not in pending:
                problems.append(f"{where}message {i}: tool result for unknown call {tid}")
            pending.discard(tid)
        elif role == "user" and not (m.get("content") or "").strip():
            problems.append(f"{where}message {i}: empty user turn")
    return problems


def to_hf_messages(ex: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert a godotai example into the OpenAI-style structure HF chat templates accept."""
    out: list[dict[str, Any]] = []
    if ex.get("system"):
        out.append({"role": "system", "content": ex["system"]})
    for m in ex["messages"]:
        role = m["role"]
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = [{"type": "function", "id": tc.get("id") or f"call_{i}",
                                        "function": {"name": tc["name"], "arguments": tc.get("args") or {}}}
                                       for i, tc in enumerate(m["tool_calls"])]
            out.append(entry)
        elif role == "tool":
            content = m.get("content") or ""
            if m.get("is_error"):
                content = "ERROR: " + content
            out.append({"role": "tool", "content": content, "tool_call_id": m.get("tool_call_id") or "", "name": ""})
        else:
            out.append({"role": "user", "content": m.get("content") or ""})
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                               "parameters": t.get("input_schema") or {"type": "object"}}}
             for t in ex.get("tools") or []]
    return out, tools


def to_conversational_row(ex: dict[str, Any]) -> dict[str, Any]:
    """Row for TRL's *conversational* dataset format (``messages`` [+ ``tools``]).

    TRL applies the chat template itself and, with ``SFTConfig(assistant_only_loss=True)``,
    computes the loss on assistant turns only — user prompts and tool/engine results are
    context, not targets. Needs a chat template with ``{% generation %}`` markers; TRL
    patches known model families (e.g. Qwen3) automatically (TRL SFT docs, 2026-09-26).
    """
    msgs, tools = to_hf_messages(ex)
    row: dict[str, Any] = {"messages": msgs}
    if tools:
        row["tools"] = tools
    return row


def render_plain(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    """Template-free rendering (fallback + the thing --dry-run measures)."""
    parts: list[str] = []
    if tools:
        parts.append("<tools>\n" + json.dumps(tools, ensure_ascii=False) + "\n</tools>")
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = "\n".join("<tool_call>" + json.dumps({"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
                                                          ensure_ascii=False) + "</tool_call>" for tc in m["tool_calls"])
            parts.append(f"<|{m['role']}|>\n{m.get('content') or ''}\n{calls}".rstrip())
        elif m["role"] == "tool":
            parts.append(f"<|tool_result|>\n{m['content']}")
        else:
            parts.append(f"<|{m['role']}|>\n{m['content']}")
    return "\n".join(parts) + "\n"


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def load_examples(path: Path, min_iterations: int = 0) -> tuple[list[dict[str, Any]], list[str]]:
    kept: list[dict[str, Any]] = []
    problems: list[str] = []
    for n, ex in enumerate(read_jsonl(path), 1):
        p = validate_example(ex, where=f"line {n}: ")
        if p:
            problems.extend(p)
            continue
        if int(ex.get("iterations") or 0) < min_iterations:
            continue
        kept.append(ex)
    return kept, problems


def dry_run(args: argparse.Namespace) -> int:
    examples, problems = load_examples(Path(args.data), args.min_iterations)
    for p in problems[:30]:
        print("  !", p)
    if not examples:
        print(f"dry-run: no usable examples in {args.data}" + (f" ({len(problems)} problems)" if problems else ""))
        return 1
    lengths: list[int] = []
    tool_turns = 0
    verified = 0
    for ex in examples:
        msgs, tools = to_hf_messages(ex)
        lengths.append(approx_tokens(render_plain(msgs, tools)))
        tool_turns += sum(1 for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
        verified += bool(ex.get("verification_passed"))
    lengths.sort()
    over = sum(1 for l in lengths if l > args.max_seq_length)
    print(f"dry-run: {len(examples)} usable example(s), {len(problems)} rejected line(s)")
    print(f"  engine-verified: {verified}/{len(examples)}   assistant turns with tool calls: {tool_turns}")
    print(f"  approx tokens/example: min {lengths[0]}  median {lengths[len(lengths) // 2]}  max {lengths[-1]}  "
          f"total {sum(lengths):,}")
    print(f"  examples longer than --max-seq-length {args.max_seq_length}: {over} (they would be truncated)")
    if verified < len(examples):
        print("  note: unverified examples present — extract with the default filter unless you want repair-only data")
    return 0


# ---------------------------------------------------------------------------
# Training (requires torch / transformers / peft / trl / datasets / bitsandbytes)
# ---------------------------------------------------------------------------
def train(args: argparse.Namespace) -> int:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:  # pragma: no cover - depends on the environment
        print(f"training dependencies missing: {exc}\n"
              "pip install torch transformers peft trl datasets bitsandbytes accelerate", file=sys.stderr)
        return 2

    examples, problems = load_examples(Path(args.data), args.min_iterations)
    if problems:
        print(f"{len(problems)} line(s) rejected by validation (run --dry-run for details)")
    if not examples:
        print("no usable examples", file=sys.stderr)
        return 1

    bf16_ok = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    compute_dtype = torch.bfloat16 if bf16_ok else torch.float16
    print(f"device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}  "
          f"compute dtype: {compute_dtype}  gpus: {torch.cuda.device_count()}")

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=args.trust_remote_code)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def render(ex: dict[str, Any]) -> dict[str, str]:
        msgs, tools = to_hf_messages(ex)
        try:
            text = tokenizer.apply_chat_template(msgs, tools=tools or None, tokenize=False)
        except Exception:  # template without tool support → plain rendering
            text = render_plain(msgs, tools)
        return {"text": text}

    if args.assistant_only_loss:
        ds = Dataset.from_list([to_conversational_row(ex) for ex in examples])
    else:
        ds = Dataset.from_list([render(ex) for ex in examples])
    if args.eval_fraction > 0 and len(ds) >= 10:
        split = ds.train_test_split(test_size=args.eval_fraction, seed=args.seed)
        train_ds, eval_ds = split["train"], split["test"]
    else:
        train_ds, eval_ds = ds, None
    print(f"train examples: {len(train_ds)}  eval examples: {len(eval_ds) if eval_ds is not None else 0}")

    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=compute_dtype)
    load_kwargs: dict[str, Any] = dict(quantization_config=quant, device_map="auto", attn_implementation="sdpa",
                                       trust_remote_code=args.trust_remote_code)
    try:  # transformers ≥ 4.56 / v5: `dtype`; `torch_dtype` is deprecated but still accepted
        model = AutoModelForCausalLM.from_pretrained(args.model, dtype=compute_dtype, **load_kwargs)
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=compute_dtype, **load_kwargs)
    model.config.use_cache = False

    lora = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout, bias="none",
                      task_type="CAUSAL_LM", target_modules="all-linear")
    cfg = SFTConfig(
        output_dir=args.out, num_train_epochs=args.epochs, per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum, learning_rate=args.lr, lr_scheduler_type="cosine",
        warmup_ratio=0.03, logging_steps=10, save_steps=args.save_steps, save_total_limit=2,
        eval_strategy="steps" if eval_ds is not None else "no", eval_steps=args.save_steps if eval_ds is not None else None,
        bf16=bf16_ok, fp16=not bf16_ok, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, max_length=args.max_seq_length,
        dataset_text_field="text", packing=False, report_to=[], seed=args.seed, optim="paged_adamw_8bit",
        assistant_only_loss=bool(args.assistant_only_loss),
    )
    trainer = SFTTrainer(model=model, args=cfg, train_dataset=train_ds, eval_dataset=eval_ds,
                         processing_class=tokenizer, peft_config=lora)
    trainer.train(resume_from_checkpoint=args.resume)
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"adapter saved to {args.out}")
    if args.merge:
        merged = trainer.model.merge_and_unload()
        merged_dir = os.path.join(args.out, "merged")
        merged.save_pretrained(merged_dir, safe_serialization=True)
        tokenizer.save_pretrained(merged_dir)
        print(f"merged model saved to {merged_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, help="JSONL from `godotai dataset extract`")
    p.add_argument("--dry-run", action="store_true", help="validate + statistics only (no ML dependencies)")
    p.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct", help="base model id or local path")
    p.add_argument("--out", default="training/output/godotai-lora")
    p.add_argument("--max-seq-length", type=int, default=4096)
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--save-steps", type=int, default=50)
    p.add_argument("--eval-fraction", type=float, default=0.05)
    p.add_argument("--min-iterations", type=int, default=2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--merge", action="store_true", help="also save the merged fp16 model (needs extra disk)")
    p.add_argument("--resume", default=None, help="checkpoint dir to resume from")
    p.add_argument("--trust-remote-code", action="store_true")
    p.add_argument("--assistant-only-loss", action="store_true",
                   help="hand TRL the conversational dataset and compute loss on assistant turns only "
                        "(needs a chat template with {%% generation %%} markers; TRL patches known families)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    return train(args)


if __name__ == "__main__":
    raise SystemExit(main())
