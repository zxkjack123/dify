# Knowledge Pipeline Templates

This folder contains custom RAG Pipeline templates (.rag.yml) that can be imported into Dify Console.

Template:
- file-parentchild-external-embedding-hybrid.rag.yml
  - Parent-child chunking for files using an external OpenAI-compatible embedding provider with model `sudon-sd-bge-m3`.
  - Hybrid retrieval (vector + keyword) without reranking by default.

How to import:
1) Open Dify Console → Knowledge → Pipelines → Import.
2) Select this YAML and import it.
3) After import, open the Knowledge Base node and verify:
   - Embedding provider name is `langgenius/openai_api_compatible/openai_api_compatible`.
   - Embedding model name is `sudon-sd-bge-m3`.
   - Retrieval method is `Hybrid`.

Requirements:
- In Model Providers, add a new Embedding model under the provider "OpenAI-API-compatible" with:
  - Base URL: your external compatible endpoint (e.g. https://your.endpoint/v1)
  - API Key: your key (if required by the endpoint)
  - Model Name: `sudon-sd-bge-m3` (or map an endpoint model to this display name)

Notes:
- The parent delimiter defaults to `\n# ` (note the trailing space) which matches heading-based MinerU Markdown segmentation.
- Child delimiter defaults to `\n`.
- You can adjust chunk lengths in the shared variables section after import.
