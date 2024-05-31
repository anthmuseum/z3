import argparse
import sqlite3
import random
import zipfile
import tempfile
import json
import magic
import glob
import os
from html2image import Html2Image

from json2html import json2html
from preview_generator.manager import PreviewManager
# see dependencies at https://pypi.org/project/preview-generator/

"""
zoo.py: Z3 Object-Oriented application interface.

This will construct Library, Collection, and Item objects with associated
methods, by querying an sqlite database containing RDF triples.
"""

class Config(object):
    database = 'z3.db'
    creator_types = [
            "artist", "attorneyAgent", "author", "bookAuthor",
            "cartographer", "castMember", "commenter", "composer",
            "contributor", "cosponsor", "counsel", "director", "editor",
            "guest", "interviewee", "interviewer", "inventor", "performer",
            "podcaster", "presenter", "producer", "programmer", "recipient",
            "reviewedAuthor", "scriptwriter", "seriesEditor", "sponsor",
            "translator", "wordsBy" ]
    list_fields = creator_types + ['tag', 'collection']
        #: Fields that we load on demand only
        #: Set to an empty list to load everything
    ignore_fields = ['file', 'thumb', 'preview', 'annotationSortIndex', 'annotationColor', 'annotationPosition', 'version']
    thumb_sizes = {'thumb': (400, 600), 'preview': (800, 600)}
    schema = 'zotero'

cfg = Config()

def _make_key():
    # This creates a Zotero-compatible key
    alphabet = list('ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    return ''.join(random.choices(alphabet, k=8))

def _get_parent(field, keys, con=None):
    # pass the connection so we can keep it open during recursion
    if not con:
        con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select object from metadata where subject = ? and predicate = ?", (keys[0], field))
    parent = c.fetchone()
    if not parent:
        con.close()
        return keys
    else:
        keys.insert(0, parent[0])
        return _get_parent(field, keys, con)

def _get_thumb(field, itemKey, attachment=False):
    # field must be "thumb" or "preview"
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    keys = [itemKey]
    if not attachment:
        keys.extend( _get_children(itemKey) )
    for key in keys:
        c = con.execute("select object from metadata where subject = ? and predicate = ?", (key, field))
        thumb = c.fetchone()
        if thumb:
            thumb_data = thumb[0] # store before closing the connection
            con.close()
            return thumb_data
    con.close()
    # no thumb found; create a new one
    return _make_thumb(field, keys)

def _make_thumb(field, keys):
    """Create a thumbnail image for the first item in the list "keys"
    that has a binary file attachment."""

    (w, h) = cfg.thumb_sizes.get(field, (400, 400))
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    for key in keys:
        c = con.execute("select object from metadata where subject = ? and predicate = 'file'", (key,))
        file_data = c.fetchone()
        if file_data:
            with tempfile.NamedTemporaryFile() as fp:
                fp.write(file_data[0])
                del file_data # free memory
                src = fp.name
                mime = magic.Magic(mime=True)
                mtype = mime.from_file(fp.name)
                with tempfile.TemporaryDirectory() as tmpdir:
                    # extract the html files, which are stored in zip
                    if mtype == 'application/zip':
                        with zipfile.ZipFile(fp.name, 'r') as z:
                            z.extractall(tmpdir)
                        html = glob.glob(os.path.join(tmpdir,'*.html'))
                        if not html:
                            con.close()
                            return None
                        src = html[0]
                        thumb_file = os.path.join(tmpdir, 'out.jpg')

                        hti = Html2Image()
                        hti.output_path = tmpdir
                        # This gives lots of exceptions... aborting here
                        # prevents us from writing to the database!
                        hti.screenshot(url=src, save_as='out.jpg',
                            size=[(w,h)])
                        if not os.path.exists(thumb_file):
                            con.close()
                            print("{}: error generating thumbnail".format(key))
                            return None
                    else:
                        manager = PreviewManager(tmpdir, create_folder=True)
                        try:
                            thumb_file = manager.get_jpeg_preview(src, height=h, width=w)
                        except:
                            con.close()
                            print("{}: error generating thumbnail".format(key))
                            return None
                    with open(thumb_file, 'rb') as t:
                        thumb_data = t.read()
            con.execute("insert or replace into metadata(subject, predicate, object) values (?, ?, ?)", (key, field, sqlite3.Binary(thumb_data)))
            con.commit()
            con.close()
            return thumb_data
    return None

def _get_collections():
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select subject from metadata where predicate = 'itemType' and object = 'collection'")
    collections = [row[0] for row in c]
    con.close()
    return collections


def _get_collection_members(itemKey, offset, limit):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select subject from metadata where predicate = 'collection' and object = ? limit ? offset ?", (itemKey, limit, offset))
    children = [row[0] for row in c]
    con.close()
    return children

def _get_collection_members_count(itemKey):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select count(*) from metadata where object = ? and predicate = 'collection'", (itemKey,))
    count = c.fetchone()
    if count:
        return count[0]
    else:
        return 0

def _get_children(itemKey):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select subject from metadata where object = ? and predicate = 'parentItem'", (itemKey, ))
    children = [row[0] for row in c]
    con.close()
    return children

def _get_attrs(predicate):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select distinct object from metadata where predicate = ?", (predicate, ))
    data = sorted([row[0] for row in c], key=str.lower)
    con.close()
    return data

def _query(predicate, object):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    if '*' in object:
        c = con.execute("select subject from metadata where predicate = ? and object like ?", (predicate, object.replace('*', '%') ))

    else:
        c = con.execute("select subject from metadata where predicate = ? and object = ?", (predicate, object ))
    data = [row[0] for row in c]
    con.close()
    return data


def _get_attr(subject, predicate):
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select object from metadata where subject = ? and predicate = ?", (subject, predicate ))
    data = c.fetchone()
    con.close()
    if data:
        return data[0]
    return data


def _get_file(key):
    # FIXME: This reads the entire field into memory
    # For larger blobs we will want to stream the data.
    # rowid = con.execute("select rowid from metadata where ...")
    # with con.blobopen('metadata', 'object', rowid) as blob: ...
    # while True:
    # blob.read(length=1028)
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select object from metadata where subject = ? and predicate = 'file'", (key, ))
    file_data = c.fetchone()
    con.close()
    if file_data:
        return file_data[0]
    return file_data

def _truncate(string, max=250):
    if len(string) < max:
        return string
    return string[:max] + ' ...'

class Item:
    def __init__(self, key=None):
        self.title = None

    def children(self):
        return _get_children(self.key)

    def file(self):
        if self.itemType != 'attachment':
            return None
        return _get_file(self.key)

    def ancestors(self):
        # find parent item for file, note, attachment, etc.
        return _get_parent('parentItem', [self.key])

    def thumbnail(self):
        return _get_thumb('thumb', self.key)

    def thumbnail_preview(self):
        return _get_thumb('preview', self.key)

    def json(self):
        return json.dumps(self.__dict__)

    def html(self):
        """Return an html fragment containing a table listing the metadata for the item."""
        # replace keys with labels
        # item_types = zot.item_types()
        # item_types_dict = {type['itemType']: type['localized'] for type in item_types}

        # turn of escape; assume that we trust the incoming data
        # this allows proper display of notes, etc. from Zotero
        j = self.__dict__
        return json2html.convert(json=j, escape=False,
                table_attributes='class="table"')

    def identifier(self):
        # uuid
        pass

    def csv(self):
        pass


class Collection:
    def __init__(self, key=None):
        self.name = None

    def members(self, offset=0, limit=20):
        return _get_collection_members(self.key, offset, limit)

    def members_count(self):
        return _get_collection_members_count(self.key)

    def html(self):
        return json2html.convert(json=self.__dict__, escape=False,
                table_attributes='class="table"')

class Library:
    def __init__(self, key=None):
        self.name = None
        self.itemType = None
        self.url = None
        self.description = None

    def html(self):
        return json2html.convert(json=self.__dict__, escape=False,
                table_attributes='class="table"')

########################

def load_library_data():
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select subject from metadata where predicate = 'itemType' and object = 'library'")
    result = c.fetchone()
    con.close()
    if result:
        return load(result[0])
    return None

def load(itemKey):
    """Create a dictionary out of the metadata in the sqlite database."""
    con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
    c = con.execute("select object from metadata where subject = ? and predicate = 'itemType'", (itemKey,))
    result = c.fetchone() or (None,)
    itemType = result[0]
    if itemType == 'collection':
        i = Collection()
    elif itemType == 'library':
        i = Library()
    else:
        i = Item()

    c = con.execute("select subject, predicate, object, typeof(object) from metadata where subject = ?", (itemKey, ))
    for row in c:
        if row[3] == 'blob':
            continue # avoid loading blobs, etc. into memory
        if row[1] in cfg.ignore_fields:
            continue
        if row[1].startswith('.'):
            continue # ignore internal data
        v = getattr(i, row[1], None)
        # force these values to lists, even if there is a single
        # value
        if row[1] in cfg.list_fields:
            if not v:
                setattr(i, row[1], [row[2]])
            else:
                v.append(row[2])
                setattr(i, row[1], v)
        # convert multiple value to lists
        elif v and not isinstance(list, v):
            setattr(i, row[1], [v, row[2]])
        else: #strings
            setattr(i, row[1], row[2])
    con.close()
    return i

def run(args):
    if args.generate_thumbs:
        con = sqlite3.connect('file:{}?mode=rw'.format(cfg.database), uri=True)
        c = con.execute("select subject from metadata where predicate = 'file'")
        keys = [row[0] for row in c]
        con.close()
        for key in keys:
            print("Processing {}...".format(key))
            _get_thumb('thumb', key, attachment=True)
            _get_thumb('preview', key, attachment=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Interact with Zotero library')
    parser.add_argument('--database', default='z3.db')
    parser.add_argument('--generate-thumbs', action='store_true')
    args = parser.parse_args()
    run(args)
