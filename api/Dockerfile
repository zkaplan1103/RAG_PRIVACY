# PolicyLens Lambda container image
#
# Base: AWS Lambda Python 3.11 (al2023, x86_64)
# Models baked in at build time so cold start never downloads them.
#
# Image size estimate:
#   Lambda base        ~180 MB
#   torch (CPU only)   ~700 MB   (cpu-only wheel; see below)
#   sentence-transformers + bge-small weights   ~140 MB
#   bge-reranker-base weights                   ~570 MB
#   policylens package + deps (anthropic, chromadb, psycopg, langfuse, etc.)  ~200 MB
#   -------
#   Total (uncompressed): ~1.8 GB
#   Lambda supports container images up to 10 GB — well within limit.
#
# SETUP: build with:
#   IMAGE_VERSION=$(git rev-parse --short HEAD)
#   docker build --build-arg IMAGE_VERSION=$IMAGE_VERSION -t policylens-lambda:$IMAGE_VERSION api/
#
# SETUP: push to ECR:
#   aws ecr get-login-password --region $AWS_REGION | \
#     docker login --username AWS --password-stdin $ECR_REGISTRY
#   docker tag policylens-lambda:$IMAGE_VERSION $ECR_REGISTRY/policylens-lambda:$IMAGE_VERSION
#   docker push $ECR_REGISTRY/policylens-lambda:$IMAGE_VERSION
#
# Then update Lambda image URI in Terraform and re-apply.

FROM public.ecr.aws/lambda/python:3.11

ARG IMAGE_VERSION=dev
ENV IMAGE_VERSION=${IMAGE_VERSION}
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Hugging Face cache dir — models are downloaded here during build
ENV HF_HOME=/opt/hf_cache
ENV TRANSFORMERS_CACHE=/opt/hf_cache

WORKDIR /var/task

# ---------------------------------------------------------------------------
# System deps (minimal — Lambda base has yum)
# ---------------------------------------------------------------------------
RUN yum install -y git && yum clean all

# ---------------------------------------------------------------------------
# Python deps
# Install torch CPU-only first (avoids pulling CUDA wheels, saves ~2 GB)
# ---------------------------------------------------------------------------
COPY pyproject.toml ./
COPY uv.lock ./

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Install CPU torch first (smaller) before the rest of the deps
RUN pip install --no-cache-dir \
        torch==2.2.2+cpu \
        --index-url https://download.pytorch.org/whl/cpu

# Install all other project dependencies (excludes torch since already present)
# psycopg binary + langfuse + sentence-transformers + anthropic etc.
RUN pip install --no-cache-dir \
        anthropic \
        "chromadb>=0.5.0" \
        "sentence-transformers>=2.7.0,<3.0.0" \
        "psycopg[binary,pool]>=3.3.4" \
        "langfuse>=2.0.0,<3.0.0" \
        boto3 \
        "numpy>=1.24,<2.0" \
        lxml \
        beautifulsoup4

# ---------------------------------------------------------------------------
# Download and cache HF models at BUILD TIME
# This eliminates model download from the cold-start path.
# bge-small: ~140 MB; bge-reranker-base: ~570 MB
# ---------------------------------------------------------------------------
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
print('Downloading BAAI/bge-small-en-v1.5 ...'); \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
print('Downloading BAAI/bge-reranker-base ...'); \
CrossEncoder('BAAI/bge-reranker-base'); \
print('Models cached to /opt/hf_cache'); \
"

# ---------------------------------------------------------------------------
# Copy policylens source
# ---------------------------------------------------------------------------
COPY src/policylens/ ./policylens/
COPY api/handler.py ./

# ---------------------------------------------------------------------------
# Lambda entrypoint
# handler.handler is the function Lambda calls.
# ---------------------------------------------------------------------------
CMD ["handler.handler"]
