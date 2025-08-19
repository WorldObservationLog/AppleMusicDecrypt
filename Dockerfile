FROM python:3-slim

WORKDIR /app

COPY . /app

# Install Poetry
RUN set -eux; \
    apt-get update; \
    apt-get install -y bash curl; \
    apt-get clean; \
    \
    curl -sSL https://install.python-poetry.org | python3 -

ENV PATH="/root/.local/bin:$PATH"

# Build GPAC and Bento4
RUN set -eux; \
    apt-get update; \
    apt-get install -y build-essential pkg-config git cmake zlib1g-dev; \
    \
    # Build and install GPAC
    \
    git clone --depth=1 https://github.com/gpac/gpac.git ./build/gpac || exit 1; \
    cd ./build/gpac; \
    ./configure --static-bin; \
    make -j$(nproc); \
    make install; \
    MP4BOX_PATH=$(command -v MP4Box); \
    if [ -n "$MP4BOX_PATH" ]; then ln -sf "$MP4BOX_PATH" "$(dirname "$MP4BOX_PATH")/mp4box"; fi; \
    cd /app; \
    \
    # Build and install Bento4
    \
    git clone --depth=1 https://github.com/axiomatic-systems/Bento4.git ./build/Bento4 || exit 1; \
    mkdir -p ./build/Bento4/cmakebuild; \
    cd ./build/Bento4/cmakebuild; \
    cmake -DCMAKE_BUILD_TYPE=Release ..; \
    make -j$(nproc); \
    make install; \
    cd /app; \
    \
    # Clean up
    \
    rm -rf ./build; \
    apt-get autoremove --purge -y build-essential pkg-config git cmake zlib1g-dev; \
    apt-get clean

# Install Python dependencies
RUN set -eux; \
    apt-get update; \
    apt-get install -y build-essential ffmpeg; \
    \
    poetry install; \
    \
    apt-get autoremove --purge -y build-essential; \
    apt-get clean

CMD ["poetry", "run", "python", "main.py"]