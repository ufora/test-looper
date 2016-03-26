import os

class DirectoryScope(object):
    def __init__(self, directory):
        self.directory = directory

    def __enter__(self):
        self.originalWorkingDir = os.getcwd()
        os.chdir(self.directory)

    def __exit__(self, exc_type, exc_value, traceback):
        os.chdir(self.originalWorkingDir)
