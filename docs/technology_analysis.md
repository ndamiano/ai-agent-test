# Technology watchlist

New models improve output; new inference shells lower cost. Both compound, and neither announces
itself — so the stack gets re-validated against what has shipped, on a schedule rather than on a
hunch.

A candidate stays here until it has been RUN against real builds. Then the numbers and the ruling go
to `docs/experiments.md` and the entry moves to "Previously investigated" at the bottom — including
the ones we reject, because "why we didn't switch" is the expensive thing to relearn.

## What we run today

| Job | Model | Served by | Defined in |
|---|---|---|---|
| Build turns (the LLM) | Qwen3.8 27B, NVFP4 | ninfer on a 5090; llama.cpp elsewhere | `llm.model` in settings.json |
| Sprites + scenes | NetaYume Lumina v4 | ComfyUI | `workflows/txt2img_item.json` |
| Tiles | DreamShaperXL Turbo v2.1 → min-cut quilting | ComfyUI + `tools/quilting.py` | `workflows/txt2img_tile.json` |
| Scene-chain subjects | Qwen-Image 2512 | ComfyUI | `workflows/txt2img_subject.json` |
| Scene-chain terrain | DreamShaperXL Turbo v2.1 (masked img2img) | ComfyUI | `workflows/img2img_terrain.json` |
| Scene-chain embed | Qwen-Image-Edit 2511 | ComfyUI | `workflows/imgedit_scene.json` |
| 3D meshes | TRELLIS 2 | home-rolled runtime, `tools/trellis_server.py` | — |
| Render safety scoring | small timm ViT, locally exported | in the image worker | `scripts/export_safety_model.py` |
| Sound effects, music, voice | **nothing** | — | — |

A `kind` picks the model and the workflow (`tools/comfyui_tools._kind_recipe`). Audio is the one
whole category we do not produce at all, and it is wanted.

## Things to validate
- [ ] Inflect-nano-v2 | inflect-micro-v2 for voice gen. I'm not sure if this will be useful as it's only a single voice,
      but it might be valuable either way. We can look into using this as a way to train our own voice models?
      https://www.reddit.com/r/LocalLLaMA/comments/1v5ve6v/i_released_inflect_v2_two_ultratiny_complete_tts/
      https://huggingface.co/owensong/Inflect-Micro-v2
- [ ] TensorSharp. It claims that it gets a speed boost on prefill and TTFT, though slightly lower decode vs llama.cpp
      on CUDA. Worth investigating either way.
      NOTE: the published comparison is against llama.cpp, which is not our 5090 baseline — ninfer
      is, at ~3x llama.cpp. Prefill/TTFT is still the right thing to watch: a build's input IS its
      transcript, so prefill is most of a turn.
      https://www.reddit.com/r/LocalLLaMA/comments/1v6ect8/benchmarks_tensorsharp_vs_llamacpp/
      https://github.com/zhongkaifu/TensorSharp
- [ ] Iimg2threejs seems to be a nifty thing, where it builds a threejs group proceedurally based on an image. If it's
      capable it might be worth seeing how it compares to trellis. If it's faster / easier to setup / similar results
      it might be worth switching to. At minimum worth investigating.
      https://www.reddit.com/r/TopologyAI/comments/1v4izw6/opensource_imageto3d_now_generates_editable/
      https://github.com/img2threejs/img2threejs
- [ ] TripoSplat is worth investigating to see if it's viable. It's opensource and MIT Licensed. It seems to be high
      quality. Worth a look. 
      https://www.reddit.com/r/TopologyAI/comments/1v3j18g/best_free_imageto3d_gaussian_splat_generator_is/
      https://github.com/VAST-AI-Research/TripoSplat
- [ ] Lato.2 seems interesting. It generates 3d models as parts, which will help with automated animation probably.
      https://www.reddit.com/r/TopologyAI/comments/1v91x4h/opensource_3d_ai_generates_meshes_with/
      https://lohhhha.github.io/LATO.2/
- [ ] Vibe voice from microsoft for voiceovers
      https://github.com/microsoft/VibeVoice
- [ ] TriFlow, a better way to decimate models? I'm not sure, but worth looking into.
      https://www.reddit.com/r/TopologyAI/comments/1vd7149/new_ai_retopology_method_generates_clean/
      https://derkleineli.github.io/triflow/#
- [ ] Minimax H3 video generation. Maybe not relevant? Maybe relevant? Worth investigating either way.
      NOTE: already verified to RUN locally on the 5090 (video + audio, in mess-with-comfy) — VRAM
      is tight. So the open question is not "can we run it" but what a game does with video that a
      canvas cannot.
      https://www.reddit.com/r/StableDiffusion/comments/1vdoqb9/repost_because_not_everyone_can_read_blurry/
- [ ] RTX 6000 optimizations
      https://github.com/local-inference-lab/rtx6kpro
- [ ] Scenema voice, seems legit good!
      https://www.reddit.com/r/LocalLLaMA/comments/1vgfmee/scenema_audio_comes_to_comfyui_runs_on_8gb_vram/
      https://huggingface.co/ScenemaAI/scenema-audio
- [ ] Shieldstral, might be good for safety filtering?
      https://huggingface.co/mistralai/Shieldstral-1.0-3B
      NOTE: this is the only candidate on the list that touches a LIVE fail-closed seam. Swapping it
      is not a like-for-like trial — a safety model is judged on its false-NEGATIVE rate at a fixed
      false-positive budget, and the current classifier's threshold was set that way over 290
      renders (`docs/experiments.md`, 2026-08-03). Same method or no swap.
- [ ] For music generation we might want to try minimax music 3
      https://www.reddit.com/r/comfyui/comments/1vnf0p2/comfyorgminimaxmusic3_hugging_face_now_online/

## Previously investigated

- **Inference shell: llama.cpp → ninfer** (adopted 2026-08-01). ~194 vs ~63 tok/s on the same
  weights on a 5090. ninfer serves Qwen only, on 5090s only, and speaks `chat` only, so llama.cpp
  stays the fallback everywhere else. Thinking and sampling are launch flags on both — see
  `docs/local_dev.md`.
- **Image models: flux1-schnell-fp8 and animaOfficial retired** (bake-off 2026-08-06). Replaced by
  per-kind routing: NetaYume Lumina for sprites/scenes, Qwen-Image 2512 for objects/scenes,
  DreamShaperXL Turbo for tile textures. Both run at a real cfg, so the negative prompt is live on
  every kind. animaOfficial's weights are non-commercial, so its orphaned workflow was deleted
  outright (2026-08-08) rather than left to be picked up again — every model in the table above is
  permissively licensed on the WEIGHTS, not just the code.
- **Audio: surveyed and measured, deliberately not implemented** (2026-08-01, 58 generations —
  `docs/experiments.md`). ACE-Step 1.5 (MIT, code + weights) does 60 s of music in 1.8 s with the
  planner off; MOSS-SoundEffect v2.0 (Apache 2.0) does SFX in 2–3 s at 19.6 GB resident. Ruled out
  on WEIGHTS licensing, whatever the code says: Stable Audio, MusicGen/AudioGen/AudioCraft,
  AudioLDM 2, Tango 2. Music ships before SFX, because MOSS needs a full TRELLIS-shaped lift while a
  WebAudio oscillator beats a diffusion model at a 0.2 s arcade blip.

  **Read that entry before trialling any audio candidate above** — it already holds the license
  method, the integration shape, and three environment traps (Blackwell needs
  `--use-pytorch-cross-attention`, the box has no ffmpeg, and ComfyUI's node cache will fake your
  numbers unless you unload between arms).
