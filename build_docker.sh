#!/bin/sh

set -e

echo "Building container tagged deaddocker. This will take some time..." 
docker build . -t deaddocker

echo "Creating docker volume named deadpersistent..." 
docker volume create deadpersistent

echo "Preparing volume..."
docker run -it \
    -v deadpersistent:/persistent\
    -v $(realpath ./patches/):/patches \
    deaddocker \
    sudo su -c "cp /patches/patchdb.json /persistent/patchdb.json &&\
                (if [[ ! -d /persistent/logs ]]; then mkdir /persistent/logs; fi) &&\
                (if [[ ! -d /persistent/compiler_cache ]]; then mkdir /persistent/compiler_cache; fi) &&\
                chown dead:dead -R /persistent"
docker run -it \
    -v deadpersistent:/persistent\
    deaddocker \
    sh -c "chmod 770 /persistent/compiler_cache &&\
           chmod g+rws /persistent/compiler_cache"

docker run -it \
    -v deadpersistent:/persistent\
    deaddocker \
    sh -c "touch /persistent/casedb.sqlite3"

docker run -it \
    -v deadpersistent:/persistent\
    deaddocker \
    sh -c "cd /persistent && (if [[ ! -d gcc ]]; then git clone git://gcc.gnu.org/git/gcc.git; fi)"

docker run -it \
    -v deadpersistent:/persistent\
    deaddocker \
    sh -c "cd /persistent && (if [[ ! -d llvm-project ]]; then git clone https://github.com/llvm/llvm-project; fi)"
