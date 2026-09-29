SAVE_CHECKED = """
import os


def _save_checked(image, path):
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        raise FileNotFoundError("cannot save %s: the folder %s does not exist" % (path, folder))
    saved = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(path))
    if not saved or not os.path.isfile(path):
        raise OSError("GIMP could not write %s" % path)
"""
