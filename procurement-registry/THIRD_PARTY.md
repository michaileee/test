# Third-party components

- CPython 3.13.17, official Windows x64 embeddable distribution:
  https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip .
  PSF license and bundled component notices are retained in runtime/LICENSE.txt.
  This includes the runtime's own third-party components such as OpenSSL.
- Requests, urllib3, Beautiful Soup, charset-normalizer, idna, certifi, soupsieve,
  and typing-extensions: exact versions and wheel hashes are in
  procurement_app/requirements-portable.lock.txt. Their *.dist-info license files
  are retained inside runtime/dependencies.zip.
- Noto Sans Georgian: SIL Open Font License; see procurement_app/fonts/OFL.txt.
- Zig 0.17.0 is a build tool only and is not included in the end-user payload.
  Its Windows support libraries are linked as needed; their licensing notices
  are retained in the upstream Zig distribution used for the build.

Project-authored code has the root MIT license. That license does not replace
the licenses of third-party components. Build metadata records the official
runtime archive hash and the bundled dependency versions and hashes.
