# Z3

This is a set of tools to support managing digital humanities resources using Zotero and sqlite.

The goal of this project is to allow teams of trusted users to manage collections of resources using Zotero as a front-end. Zotero facilitates collecting, describing, annotating, and citing resources of virtually any type.

The tools in this repository allow us to translate Zotero library data, via the Zotero API, to and from RDF triples stored in an sqlite database. The RDF storage format is useful as a simple baseline for conversions between additional forms of representation, which do not necessarily rely on the source data being available in Zotero -- something like a "pandoc for metadata". Potential conversions include:

  - DOCX finding aids
  - CSV or Excel spreadsheets containing full catalogue data
  - JSON
  - HTML
  - QDA reports (lists of quoted passages by tag)

An expectation of our current implementation is that any metadata terms created outside the Zotero frontend will be compatible with those used by Zotero (and, indirectly, CSL). The Z3 tools do not perform any validation against the Zotero schema when saving data to a local database, although the synchronizaton utility will ignore unrecognized values when uploading to Zotero. As such, it is entirely possible to use arbitrary user-defined metadata terms in the local database, or an alternative schema such as Dublin Core, but automatic translation of the terms themselves to Zotero-recognized fields is not supported. Dot-prefixed terms are used in some instances to contain local application-specific data, such as the state of most recent synchronization.

The utility will also create thumbnail images and versions of media in distribution formats. These items are not currently exposed to Zotero, though they may be converted to attachments by a future version of these tools. Thumbnails are generated on-the-fly by the web app if they cannot be found. This can create an undesirable spike in server CPU and memory usage when generating the list page for a large number of new resources. To avoid overuse of server resources, use the `--generate-thumbs` command-line argument with the `zoo.py` application, then synchronize the database to an online server. 


## REQUIREMENTS


```
$ apt install scribus rawio poppler-utils libfile-mimeinfo-perl libimage-exiftool-perl ghostscript libsecret-1-0 zlib1g-dev libjpeg-dev imagemagick libmagic1 webp exiftool
```

Install `chromium-browser` (or `google-chrome` if using wsl) to support generation of html screenshots.

## Tools

### sync.py

This utility synchronizes Zotero group libraries with a local sqlite database consisting of semantic triples.

USAGE

```
usage: sync.py [-h] [--api-key API_KEY] --library-id LIBRARY_ID
               [--version VERSION] [--get-library-data] [--database DATABASE]
               [--download-files] [--remove-deleted]
               [--merge-priority {remote,local,ignore}]

Sync with Zotero library

optional arguments:
  -h, --help            show this help message and exit
  --api-key API_KEY     API key (not required for read-only access)
  --library-id LIBRARY_ID
  --version VERSION
  --get-library-data    Download library name and description from the Zotero
                        server. This clears and replaces local library-level
                        metadata.
  --database DATABASE
  --download-files
  --remove-deleted
  --merge-priority {remote,local,ignore}
                        conflict resolution strategy. Remote: keep remote
                        values, overwriting local data. Local: keep local
                        values. Ignore: skip items with conflicting data.
                        Fields that are available in only one version are not
                        deleted.
```

EXAMPLE

    python sync.py --library-id <IDENTIFIER> --download-files --database <PATH>/<DATABASE-NAME>.db


### zoo.py

This provides an object-oriented API for a single Z3 database.

### app.py

This is a Flask web app for navigating Z3 databases, built on top of zoo.py. Type the command `flask run` to run the development server on a local machine. To run on an Apache server, install mod_wsgi, then set the path to the script in the host configuration file:

    WSGIScriptAlias /myapp /usr/local/www/wsgi-scripts/app.wsgi

