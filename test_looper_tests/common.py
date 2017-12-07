import logging
import shutil
import sys
import os

def configureLogging(verbose=False):
    loglevel = logging.INFO if verbose else logging.ERROR
    logging.getLogger().setLevel(loglevel)

    for handler in logging.getLogger().handlers:
        handler.setLevel(loglevel)
        handler.setFormatter(
            logging.Formatter(
                '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
                )
            )

def mirror_into(src_dir, dest_dir):
    for p in os.listdir(src_dir):
        if os.path.isdir(p):
            if os.path.exists(os.path.join(dest_dir, p)):
                shutil.rmtree(os.path.join(dest_dir, p))
            shutil.copytree(os.path.join(src_dir, p), os.path.join(dest_dir, p), symlinks=True)
        else:
            shutil.copy2(os.path.join(src_dir, p), os.path.join(dest_dir, p))
    for p in os.listdir(dest_dir):
        if not os.path.exists(os.path.join(src_dir, p)) and not p.startswith("."):
            if os.path.isfile(os.path.join(src_dir, p)):
                os.remove(os.path.join(src_dir, p))
            else:
                shutil.rmtree(os.path.join(src_dir, p))

