import ctypes
ctypes.cdll.LoadLibrary("libc.so.6").free(1)

