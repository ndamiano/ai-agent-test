# Worker / Image Migration

## What

The goal is to split workers and images apart, with a contract that both fulfils.

## Why

Different GPU providers require different solutions. Until we have a steady enough income stream to
utilize only a single provider, and while compute is scarce, we will need to utilize multiple
providers. Our solution for one provider may not be sufficient for using on another provider, and as
such, splitting our worker cleanly from our images that actually run the inference allows us to
build native solutions on a per platform basis.

The fusion causes headaches on deployment, because the shape of the solution is different per
provider. AWS doesn't have network drives, they have S3. Vast.ai doesn't have network drives *or* s3
so their solution has to be different from both of the previous. As we integrate more providers, we
will surely find nuances between them.

While we believe this will be net benefit, it is not without cost. This means that onboarding a new
provider is a heavier cost (we cannot simply use a different provider's image), but in reality, that
didn't work anyway. The bigger cost is that this means our wins need to be replicated across all of
our images, instead of a single image win.

## The split

Functionally, the way that this will work is the worker will own everything related to communicating
between the control plane and the actual production. This means the worker will handle getting work
as well as things like changing things formats into what's needed, for instance taking an picture 
and changing it from png to jpg, or uploading the picture to a specific directory the control plane
requires.

The image will be responsible end to end for generation. It will take whatever the prompt is, do any
required transformations, run the actual inference, and generate an output. It will follow the
contract provided, without any judgment on things like format or storage.

The final necessary thing is all images must provide a method for the worker to decommission them.
The worker should not know who the provider is, nor should they know anything about what the
decomissioning process entails. It should be an API endpoint the image provides that will be called
once the worker has been idle for the appropriate amount of time, and it will handle shutting down
the actual instance, including any required logging. 

## Contracts

### Image

#### /health

This endpoint tells the worker whether the inference is ready to pickup work. A worker is still
booting if it returns a 503 or nothing. A 410 means the box is going away, and the worker should
clean up and deregister. A 2xx response is healthy. Any other response means kill the box.

```json
{
  "reason": "Warming the model",
}
```

#### /metadata

This endpoint will return the metadata to the worker, so they can tag anything needed with it.

```json
{
  "imageId": "UUID of the build",
  "cliParams": "string of params",
  "capabilities": ["image", "mesh"],
}
```

#### /shutdown

This endpoint kills the pod. It may take some amount of time to attempt to flush logs, but the pod
will be dead shortly. If the pod fails to shutdown, it will be reaped by the scaler in the near term
future.

#### /job

The endpoint the worker calls to do the work. Synchronous post endpoint. A worker hitting the
timeout (`JOB_TIMEOUT_SECONDS`, per queue, 2x that queue's longest job) may declare the box
unhealthy and request a shutdown.

Payload:
```json
{
  "jobId": "job id from the control plane",
  "type": "llm",
  "payload": {
    // Exactly as the control plane included
  }
}
```

Result:
```json
{
  "status": "succeeded",
  "result": {result} // The type of the result depends on the type of the image
}
```

Status codes: 2xx for success, 5xx for failures

### Environment data

There are two kinds of environment data that are necessary. There are built in to the image data,
things such as `--mem-fraction-static`, or `--cuda-graph-bs`, and data points the control plane may
want to change at instantiation time, such as `context length` or `slots`. The image and worker
ignore unkown keys.

For values the control plane wants to change for the image and worker the values are passed into
the box through whatever mechanism is available (IE the env field for runpod), and the entrypoint
mounts the file to a known location. The image and worker will both pull from that known location
and utilize those values instead of their defaults.

## Repos

### ai-agent-test

This is the main GameSummoner repo. It defines the control plane, as well as many other things such
as the scaler, the prompts used, the frontend, etc.

### gamesummoner-workers

This is the worker repo. It includes:
* The claim loop
* Idle timeout
* Delivery to the control plane
* Calculating billing seconds for a job
* Logging information about the inference

### gamesummoner-images

This is the repo that holds the images for actual inference. It will include a folder per provider
with everything needed to provision models to the provider, and spin up an instance of inference. It
additionally includes all of the code to meet our contract defined above.

## Communication

In order for the pods to be as reproducable as possible, and to ensure that there are no issues with
new versions of the worker, the image will include a pinned worker hash, which it will pull once
it's been deployed.

## Migration order

Split the repos first, empty, then move one queue at a time. Normally the contract would come
before the restructure, but here the repo boundary **is** the enforcement: while `handlers.py` can
import from the same tree, the contract stays aspirational; once it cannot, every violation is a
build error.

1. Create both repos. Contract spec into `ai-agent-test`.
2. `llm` into `gamesummoner-images`, built for EC2 first. EC2 is greenfield, so this is the one
   queue where we are not refactoring and porting at the same time.
3. Worker into `gamesummoner-workers`, engine protocol knowledge stripped out into the llm adapter.
4. `image`, `video`, `mesh` follow. `anim_sheet.py` into the video image, `safety_vision.py` into
   the image image.
5. RunPod images rebuilt against the same contract. Both providers live, neither privileged.

## What the clean EC2 image drops

Every item below exists to satisfy a RunPod constraint that does not apply to EC2.

| thing | why it exists on RunPod | on EC2 |
|---|---|---|
| engine as a tarball on a volume | cut the 90-120 s image pull | caused the first boot failure |
| network volume for weights | no local disk worth using | 1.9 TB instance store sits idle |
| three-attempt retry loop | their 188 GB cgroup OOM'd about half of first launches | not our failure mode |
| image diet to 7 GB, two-stage squash | per-host pull is billed | 7 GB pulled in 33 s |
| AOT kernel promotion, baked autotune cache | every boot is a fresh container | an AMI holds this natively |
| `--source runpod`, self-terminate via their API | — | wrong provider |
| Docker itself | RunPod only accepts containers | we control the machine image |

Worth keeping regardless of provider: the prepacked loader (123 s -> 35 s, measured, with
identical tokens against a normal load) and the pack format.

## Open questions

**Fused workers.** At spot prices under $1.50/hr it becomes worth running several capabilities on
one machine. The contract already allows it — `kinds` is a list — but the economics need thought
before anything is built. `llm` alone holds 73.46 GiB of a 96 GiB card, so `llm` and `image`
cannot share one GPU; fusion means multi-GPU instances. `g7e.48xlarge` works out near
$1.03/GPU-hr, cheaper per card than the 2xlarge, and amortizes a single boot across eight cards.
That is a materially different shape from the per-job rental we have been costing.

**Where resizing actually belongs.** The delivery/production line is clean for ComfyUI parsing and
for `anim_sheet.py`. It is less obvious for "the control plane asked for 512x512 and the model
produced 1024x1024" — arguably the image knows best what it produced, arguably the worker should
own anything engine-agnostic. Resolve before the `image` queue moves.
