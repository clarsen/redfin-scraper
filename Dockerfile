FROM python:3.9

RUN apt-get update && apt-get install -y \ 
    wget \
    build-essential \
    cmake \
    gcc g++ \
    git \
    pkg-config \
    python3-dev \
    python3-numpy \
    libavcodec-dev libavformat-dev libswscale-dev \
    libgstreamer-plugins-base1.0-dev libgstreamer1.0-dev \
    libgtk2.0-dev \
    libgtk-3-dev \
    libpng-dev \
    libjpeg-dev \
    libtiff-dev \
    ghostscript

RUN \
    mkdir -p ~/opencv-source cd ~/opencv-source && \
    git clone https://github.com/opencv/opencv.git && cd opencv && \
    mkdir build && cd build && \
    cmake ../ && \
    make -j4 && \
    make install && \
    cd ~ && rm -rf opencv-source

RUN \
    useradd user \
    && mkdir /home/user \
    && chown user:user /home/user \
    && pip3 install \
    ipython \
    scipy \
    scikit-learn \
    pandas \
    matplotlib \
    xgboost \
    lightgbm \
    catboost \
    seaborn \
    jupyter \
    jupyterlab \
    beautifulsoup4 \
    lxml \
    fake-useragent \
    camelot-py \
    tabula-py \
    opencv-python \
    tqdm \
    psycopg2-binary \
    pretty_html_table \
    && pip install \
    python-dotenv

EXPOSE 8888

WORKDIR /home/user