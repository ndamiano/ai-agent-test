# Analyze new technologies to see if they're useful for our stack

Verified: 2026-07-26

## Why
While our stack is currently working, keeping on top of changes to the space is incredibly important.
New models can improve our output, new inference shells can lower our costs. We can integrate new methods
and improve the experience for our users to make more money.

## Background
Currently, we're using llama.cpp, comfyui, and a home rolled trellis runtime. The models in use are Qwen3.6 35b A3b
as our LLM, flux1-schnell-fp8, and animaOfficial_preview3Base as our image generation models, and trellis.2 as our 
3d mesh model. We are currently not producing sound effects or music, both of which are desirable. As new models
are released, we should regularly validate whether we can do better with what has been released. 

## Things to validate
- [ ] Inflect-nano-v2 | inflect-micro-v2 for voice gen. I'm not sure if this will be useful as it's only a single voice,
      but it might be valuable either way. We can look into using this as a way to train our own voice models?
      https://www.reddit.com/r/LocalLLaMA/comments/1v5ve6v/i_released_inflect_v2_two_ultratiny_complete_tts/
      https://huggingface.co/owensong/Inflect-Micro-v2
- [ ] TensorSharp. It claims that it gets a speed boost on prefill and TTFT, though slightly lower decode vs llama.cpp
      on CUDA. Worth investigating either way.
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

## Previously investigated
