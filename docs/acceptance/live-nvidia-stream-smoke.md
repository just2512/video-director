# NVIDIA live stream acceptance

This record is created to trigger the repository's opt-in live provider acceptance workflow.

Required evidence:
- Provider endpoint: https://integrate.api.nvidia.com/v1
- Model: z-ai/glm-5.3-flash
- Real SSE event and token chunk counts
- First-token latency in seconds
- HTTP status and content type
- LOCAL_MODEL_CONFIG_PRESERVATION=PASS

The workflow must report missing credentials or endpoint failures as failures. Mock responses, test doubles, and static contract checks are not acceptance evidence.

This file is an execution record placeholder; it does not assert that the live acceptance has passed.
