#!/bin/bash
# 交叉编译精简版 ffmpeg.exe（只带 MiniBox 需要的编码器/封装，静态链接）
# 需要 Linux + mingw-w64、nasm、meson、ninja。先把源码包放进 $FFB/src/：
#   ffmpeg-7.1.tar.xz  x264-stable.tar.bz2  libvpx.tar.gz(1.14.1)  lame-3.100.tar.gz  opus-1.5.2.tar.gz
#   libogg-1.3.5.tar.xz  libvorbis-1.3.7.tar.xz  dav1d-1.5.0.tar.bz2  zlib-1.3.1.tar.gz
# 产物 $FFB/ffmpeg.exe 复制到 packaging/bin/ 后再运行 PyInstaller，安装包会改用这个约 24 MB 的精简版
set -e
B=${FFB:-$PWD/ffbuild}; P=$B/prefix; S=$B/src; W=$B/work
H=x86_64-w64-mingw32
export PKG_CONFIG_PATH=$P/lib/pkgconfig PKG_CONFIG_LIBDIR=$P/lib/pkgconfig
export CFLAGS="-O2 -I$P/include" LDFLAGS="-L$P/lib -static-libgcc"
J=$(nproc)
mkdir -p $P $W; cd $W
step(){ echo "=== $1 $(date +%T)"; }
if [ ! -f $P/lib/libz.a ]; then step zlib; rm -rf zlib-1.3.1; tar xf $S/zlib-1.3.1.tar.gz; cd zlib-1.3.1
  make -f win32/Makefile.gcc PREFIX=$H- -j$J libz.a >/dev/null
  mkdir -p $P/include $P/lib/pkgconfig; cp zlib.h zconf.h $P/include; cp libz.a $P/lib
  printf 'prefix=%s\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\nName: zlib\nDescription: zlib\nVersion: 1.3.1\nLibs: -L${libdir} -lz\nCflags: -I${includedir}\n' $P > $P/lib/pkgconfig/zlib.pc; cd $W; fi
if [ ! -f $P/lib/libx264.a ]; then step x264; rm -rf x264-stable; tar xf $S/x264-stable.tar.bz2; cd x264-stable
  ./configure --host=$H --cross-prefix=$H- --prefix=$P --enable-static --disable-cli --disable-opencl --disable-avs --disable-swscale --disable-lavf --disable-ffms --disable-gpac --disable-lsmash >/dev/null
  make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libvpx.a ]; then step libvpx; rm -rf libvpx-1.14.1; tar xf $S/libvpx.tar.gz; cd libvpx-1.14.1
  CROSS=$H- ./configure --target=x86_64-win64-gcc --prefix=$P --enable-static --disable-shared --disable-examples --disable-tools --disable-docs --disable-unit-tests --disable-vp8-decoder --disable-vp9-decoder --enable-vp9 --enable-vp8 >/dev/null
  CROSS=$H- make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libmp3lame.a ]; then step lame; rm -rf lame-3.100; tar xf $S/lame-3.100.tar.gz; cd lame-3.100
  ./configure --host=$H --prefix=$P --enable-static --disable-shared --disable-frontend --disable-decoder >/dev/null
  make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libopus.a ]; then step opus; rm -rf opus-1.5.2; tar xf $S/opus-1.5.2.tar.gz; cd opus-1.5.2
  ./configure --host=$H --prefix=$P --enable-static --disable-shared --disable-doc --disable-extra-programs >/dev/null
  make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libogg.a ]; then step ogg; rm -rf libogg-1.3.5; tar xf $S/libogg-1.3.5.tar.xz; cd libogg-1.3.5
  ./configure --host=$H --prefix=$P --enable-static --disable-shared >/dev/null
  make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libvorbis.a ]; then step vorbis; rm -rf libvorbis-1.3.7; tar xf $S/libvorbis-1.3.7.tar.xz; cd libvorbis-1.3.7
  ./configure --host=$H --prefix=$P --enable-static --disable-shared --disable-docs --disable-examples >/dev/null
  make -j$J >/dev/null && make install >/dev/null; cd $W; fi
if [ ! -f $P/lib/libdav1d.a ]; then step dav1d; rm -rf dav1d-1.5.0; tar xf $S/dav1d-1.5.0.tar.bz2; cd dav1d-1.5.0
  cat > cross.txt <<X
[binaries]
c = '$H-gcc'
ar = '$H-ar'
strip = '$H-strip'
windres = '$H-windres'
pkg-config = 'pkg-config'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
X
  meson setup build --cross-file cross.txt --prefix=$P --libdir=lib --default-library=static --buildtype=release -Denable_tools=false -Denable_tests=false >/dev/null
  ninja -C build >/dev/null && ninja -C build install >/dev/null; cd $W; fi
step ffmpeg; rm -rf ffmpeg-7.1; tar xf $S/ffmpeg-7.1.tar.xz; cd ffmpeg-7.1
ENC=libx264,libvpx_vp8,libvpx_vp9,aac,libmp3lame,libopus,libvorbis,flac,pcm_s16le,pcm_s16be,pcm_s24le,gif,mjpeg,png,webvtt,ass,ssa,srt,subrip,mov_text
MUX=mp4,mov,ipod,3gp,matroska,webm,gif,mp3,wav,flac,ogg,opus,webvtt,image2,image2pipe,null,avi,flv,asf,mpegts,mpeg,vob,aiff,amr,adts,ass,srt,m4v,matroska_audio,mjpeg
FLT=buffer,buffersink,abuffer,abuffersink,scale,fps,split,palettegen,paletteuse,format,aformat,null,anull,aresample,copy,acopy,setpts,asetpts,setsar,setdar,trim,atrim,transpose,hflip,vflip,rotate,pad,crop,volume,pan,amix,channelmap,channelsplit,showinfo
./configure --target-os=mingw32 --arch=x86_64 --cross-prefix=$H- --prefix=$P --pkg-config=pkg-config --pkg-config-flags=--static \
  --extra-cflags="-I$P/include" --extra-ldflags="-L$P/lib -static" --extra-libs="-lpthread" \
  --enable-gpl --enable-version3 --disable-debug --disable-doc --disable-ffplay --disable-ffprobe --disable-network \
  --disable-avdevice --disable-postproc --disable-autodetect --enable-w32threads \
  --enable-zlib --enable-libx264 --enable-libvpx --enable-libmp3lame --enable-libopus --enable-libvorbis --enable-libdav1d \
  --disable-hwaccels --disable-encoders --enable-encoder=$ENC --disable-muxers --enable-muxer=$MUX \
  --disable-filters --enable-filter=$FLT --disable-protocols --enable-protocol=file,pipe \
  --disable-decoder=libvpx_vp8,libvpx_vp9 > ../ff-configure.log 2>&1 || { tail -30 ../ff-configure.log; exit 1; }
make -j$J >../ff-make.log 2>&1 || { tail -30 ../ff-make.log; exit 1; }
$H-strip -o $B/ffmpeg.exe ffmpeg.exe
ls -la $B/ffmpeg.exe
