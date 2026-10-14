GMP (the GNU Multiple Precision Arithmetic Library)
===================================================

bin\libgmp-10.dll is GMP {GMP_VERSION}, the unmodified build of the MSYS2 package
{GMP_PACKAGE}. It is loaded by libtomcrypt-1.dll, which the archive
extractor (bin\psp_mpq_extract.exe, through StormLib) uses.

GMP is dual-licensed under the GNU Lesser General Public License, version 3 or later
(COPYING.LESSERv3, which adds to COPYINGv3), or the GNU General Public License, version 2
or later (COPYINGv2). Kiln uses it under the LGPL.

Your rights under the LGPL:

  * libgmp-10.dll is a separate dynamic library. You may replace it with your own build of
    a compatible GMP version (same file name, 64-bit, UCRT runtime); the programs in bin\
    then use your build.
  * The complete corresponding source code of this GMP build is the MSYS2 source package
    of {GMP_PACKAGE} (the upstream GMP {GMP_VERSION} release plus the MSYS2
    build recipe), published by the MSYS2 project: https://www.msys2.org/
    The upstream source is also published by the GMP project at gmplib.org.
    On request the maintainer of Kiln provides a copy of that source package, for at least
    three years from the date this bundle was made: sandstonedevpsp@gmail.com
