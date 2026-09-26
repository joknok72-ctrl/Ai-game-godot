# Training a specialised open-weight model (optional path)

The agent in this repository is *model-agnostic*: today it runs best on Claude
Fable 5.1 (`provider = "anthropic"`), and every run leaves an append-only,
engine-verified transcript behind. This folder turns those transcripts into a
fine-tuning dataset and a QLoRA job so that, over time, a **specialised
open-weight model** can drive the same harness (`provider = "openai_compat"`).

```
.godotai/runs/*.json  ──dataset extract──►  data/sft.jsonl  ──train_qlora.py──►  LoRA adapter
       ▲                                                                              │
       └──────── engine-verified by Godot 4.7.2-stable ◄──── godotai eval run ◄───────┘
```

## What the engine gives us that no other domain has

Every training example here passed `godot --headless --import`, per-script
`--check-only` and a smoke test on the **pinned engine binary**. That is a
ground-truth correctness filter that is free, deterministic and identical to the
judge used later by `python3 -m godotai eval`. Failed attempts followed by a fix
are kept inside the same trajectory, so the model also learns *repair* (read the
engine error → change the right line → verify again). This is the same recipe
open coding-agent projects use — collect tool-use trajectories from a strong
model, keep the ones that pass hidden tests, fine-tune a smaller model, evaluate
on the same harness — applied to a domain where the "hidden test" is the engine.

## Step by step

```bash
# 1. produce runs (each one is a full transcript incl. tool calls + engine reports)
python3 -m godotai run "make a tap-dodge game for Android" --workspace games/dodge --yes
python3 -m godotai eval run --task flappy-clone --workspace games/flappy      # eval tasks are runs too

# 2. extract verified trajectories → JSONL (thinking blocks dropped, secrets redacted)
python3 -m godotai dataset extract --runs games --out data/sft.jsonl
python3 -m godotai dataset extract --runs games --out data/sft-with-repairs.jsonl --include-failed

# 3. validate offline (stdlib only)
python3 training/train_qlora.py --data data/sft.jsonl --dry-run

# 4. train on a GPU machine  (pins = the docs this script was written against, read 2026-09-26)
pip install torch "transformers>=4.57" "peft>=0.17" "trl>=1.14" datasets bitsandbytes accelerate
python3 training/train_qlora.py --data data/sft.jsonl --model Qwen/Qwen2.5-Coder-7B-Instruct \
    --out training/output/godotai-lora --max-seq-length 4096 --epochs 2
#    add --assistant-only-loss to train only on the model's own turns (plan text + tool calls);
#    user prompts and engine/tool results then count as context, not targets (TRL SFTConfig.assistant_only_loss,
#    needs a chat template with {% generation %} markers — TRL patches known families such as Qwen3 itself)

# 5. serve the adapter with any OpenAI-compatible server and point the agent at it
GODOTAI_PROVIDER=openai_compat GODOTAI_MODEL=godotai-lora GODOTAI_BASE_URL=http://localhost:8000/v1 \
    python3 -m godotai eval run --task template-baseline --workspace /tmp/eval
```

Step 5 is the only measurement that matters. Compare `evals/results/*.json`
between the hosted model and the fine-tuned one; a model that "trains well" but
fails `godot_verify` is worse, not better.

## Running step 4 on Kaggle (GPU T4 ×2)

Kaggle can run this as a **batch job**, not as an always-on service. Facts from
the Kaggle docs (read 2026-09-26):

| Fact | Source |
| --- | --- |
| GPU/CPU notebook sessions: **12 h** max; TPU sessions: **9 h**. | kaggle.com/docs/notebooks → Technical Specifications |
| "GPU T4 ×2" = 2 × NVIDIA Tesla T4, 4 CPU cores, 29 GB RAM. | same |
| `/kaggle/working`: **20 GB** persisted with the version; interactive sessions end after **20 min idle** — use *Save & Run All* / `kernels push` for long jobs. | same |
| TPU: up to **20 h per week**, 9 h per session. | kaggle.com/docs/tpu |
| `kaggle kernels push --accelerator NvidiaTeslaT4` (= GPU T4 ×2), `TpuV5E8` (= TPU v5e-8); `kernel-metadata.json` field `machine_shape`. | github.com/Kaggle/kaggle-cli docs/kernels.md, docs/kernels_metadata.md |
| Auth: `KAGGLE_API_TOKEN` env var **or** `~/.kaggle/access_token`; secrets inside a kernel come from *Add-ons → Secrets* (`kaggle_secrets.UserSecretsClient`). | docs/README.md, docs/kernels.md |

How this repo uses it — all offline-tested, **no live push was made**:

```bash
pip install kaggle
export KAGGLE_USERNAME=<your-user-slug>          # not a secret
export KAGGLE_API_TOKEN=<from kaggle.com/settings/api>   # secret: env var only, never in files/chats
kaggle datasets create -p data/                  # once: private dataset "godotai-sft-data" holding sft.jsonl
python3 scripts/kaggle_push.py --check           # offline self-check (what CI runs): renders + stages metadata, no credentials
python3 scripts/kaggle_push.py                   # checks prerequisites incl. an auth source, renders metadata, no remote call
python3 scripts/kaggle_push.py --push            # kaggle kernels push … --accelerator NvidiaTeslaT4
python3 scripts/kaggle_push.py --status
python3 scripts/kaggle_push.py --output out/     # download the adapter from /kaggle/working
```

`training/kaggle/godotai_sft_kaggle.py` is the script kernel: it installs the
ML stack, clones this repository at `GODOTAI_REF`, runs the dry-run, then the
training, and leaves the adapter in `/kaggle/working/godotai-lora`.

### Choosing the base model (checked 2026-09-26 on the Hugging Face model cards)

| Candidate | Facts from its card | Fit for 2 × T4 QLoRA |
| --- | --- | --- |
| `Qwen/Qwen2.5-Coder-7B-Instruct` (default) | dense transformer, tool-calling chat template, long track record with bitsandbytes/PEFT | **low risk** — the recipe below is the one this size is known for |
| `Qwen/Qwen3.5-9B` | 9 B, 262 144-token native context, thinking mode on by default (`<think>`), "excels in tool calling", *vision-language* model with a hybrid Gated-DeltaNet + MoE architecture, needs the latest `transformers` | **assumption, unverified**: 4-bit bitsandbytes + PEFT support for that architecture and the VLM model class were not tested here |
| `Qwen/Qwen3.6-27B` (April 2026, dense) | flagship-level coding in a 27 B dense model | **no** on 16 GB cards for training; needs ≥ 24 GB per GPU or multi-GPU sharding |
| `Qwen/Qwen3-Coder-Next` | 80 B (MoE) coding-agent model | **no** — inference-class hardware only |

The trainer is model-agnostic (`--model`), so a candidate is one flag away; the eval bank decides.

### Assumptions (not measured — flagged on purpose)

* A 7–8 B model in 4-bit with LoRA r=16 and 4 k context fits 2 × T4 (16 GB each)
  with gradient checkpointing. This is the common QLoRA experience, not a
  measurement made here. Start with `--max-seq-length 4096 --batch-size 1 --grad-accum 8`.
* T4 has no bfloat16 and no FlashAttention-2 → the script uses fp16 + SDPA.
  Expect it to be several times slower than an A100/L4.
* The TPU option (v5e-8 per the user's account, v3-8 in the older docs page) is
  **not** wired up: the QLoRA/bitsandbytes stack is CUDA-only; a TPU path would
  need a different trainer (JAX/PyTorch-XLA) and is out of scope until the GPU
  path has produced one measured result.

### What a GPU does *not* do

More compute does not make the *agent* smarter. The reasoning quality the
project leans on today comes from the hosted model's adaptive thinking at
`effort = "max"` and from the harness (exact ClassDB lookups, plan gate,
engine verification). A fine-tuned open-weight model is a **cost / autonomy /
privacy** option that must earn its place on the eval tasks.

## Files

| File | Purpose | Tested? |
| --- | --- | --- |
| `train_qlora.py` | dataset validation (`--dry-run`) and QLoRA training (`--assistant-only-loss` optional) | dry-run + dataset conversion: unit tests · training path: written against the TRL v1.14 / transformers docs, **not executed** (no GPU here) |
| `kaggle/kernel-metadata.json` | kernel metadata (`machine_shape: NvidiaTeslaT4`, private, internet on) | rendered + validated by unit tests |
| `kaggle/godotai_sft_kaggle.py` | the script kernel run on Kaggle | byte-compiled only; **not run on Kaggle** |
| `../scripts/kaggle_push.py` | `--check` self-check, prerequisite checks + `kaggle kernels push/status/output` wrapper | offline paths unit-tested and run in CI; live push **not** made |
