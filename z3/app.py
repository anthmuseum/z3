from flask import Flask, flash, send_file, request, render_template, redirect, url_for, abort
from flask_paginate import Pagination, get_page_parameter
from flask_caching import Cache
import os
import sqlite3
import datetime as dt
import base64
import re
from bs4 import BeautifulSoup
import io
import urllib.parse
import json
import toml
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash

import zoo


# settings for "public" or "private". data['type'] = PublicClosed,
# "libraryReading": "all" (/members/admin?) --> if it isn't "all", then set a password.
app = Flask(__name__)

# Default configuration values.
cfg = {
    'DEBUG' : True, # some Flask specific configs
    'CACHE_TYPE' : "FileSystemCache",  # Flask-Caching related configs
    # The cache will be cleared on application reload
    # The cache never expires by default, which is appropriate of data are
    # generally not edited after being uploaded.
    'CACHE_DEFAULT_TIMEOUT' : 0, # Seconds; 0 for non-expiring cache
    'CACHE_DIR' : ".app_cache",
    'ARCHIVE' : {
        'name': 'Digital Ethnography Archive',
        'description': '',
        'license': ''
        },
    'USERS': {}, # {'username': 'password', ...}
    'LIBRARY': {}
    }

app.config.from_mapping(cfg)
cache = Cache(app)
auth = HTTPBasicAuth()


def _init():
    """Reload the configuration file (settings.toml) and reset any calculated values. Also clear the application cache to account for new changes."""

    # /usr/var/z3-instance
    app.config.from_file(os.path.join(app.instance_path, "settings.toml"), load=toml.load)

    # FIXME: Not everything is a zotero database. Make sure we don't create links to Zotero if this isn't found.

    # slug to zotero id
    app.config['S2Z'] = {library: app.config['LIBRARY'][library]['zotero_group_id'] for library in app.config['LIBRARY']}

    # zotero id to slug
    app.config['Z2S'] = {app.config['LIBRARY'][library]['zotero_group_id']: library for library in app.config['LIBRARY']}

    with app.app_context():
        cache.clear()
    return

_init()

def _db(slug):
    """Return the database name from a URL slug"""
    try:
        db = app.config['LIBRARY'][slug.lower()]['path']
    except KeyError:
        abort(404)
    return db

@auth.verify_password
def verify_password(username, password):
    # This is low-security: passwords are saved as plain text in the
    # configuration file. But given that this is to protect read access for
    # low-risk data, it is enough for now. In future we may want to implement
    # a system for rewriting plaintext passwords and storing hashes instead,
    # every time the config loads. But this requires saving the plaintext
    # password somewhere else, which is not necessarily any more secure.
    if username and username in app.config['USERS']:
        pw = generate_password_hash(app.config['USERS'][username])
        if check_password_hash(pw, password):
            return username

@app.route('/reload')
@auth.login_required
def reload():
    """Reload the application settings.
    This can be used when settings.toml has changed, so as to avoid
    having to restart the web server. It also invalidates the cache to accommodate any changes."""

    _init()
    return "Settings reloaded"

# https://flask-httpauth.readthedocs.io/en/latest/
@app.route('/test')
@auth.login_required
def private():
    user = auth.current_user()
    return "Hello!"

def _breadcrumbs(slug=None, keys=None, link=None):
    # db_name = getattr(zoo.load(zoo.cfg.database), 'name', 'Fonds')
    crumbs = [('/', 'archive')]
    if slug:
        crumbs.extend([('/{}/collections'.format(slug), 'library')])
    if link:
        crumbs.extend([link])
    if keys:
        con = sqlite3.connect(_db(slug))
        crumbs.extend([
            ('/{}/items/{}'.format(slug, key),
            getattr(zoo.load(key), 'itemType', 'item')) for key in keys])
        con.close()
    return crumbs


def _html_preview(data, css_class="img-thumbnail mb-4"):
    if not data:
        return ''
    data64 = base64.b64encode(data).decode('utf-8')
    return '<div><img src="data:image/jpeg;base64,{}" class="{}"></div>'.format(data64, css_class)

@app.route('/')
def home():
    libraries = []
    for l in app.config['LIBRARY']:
        if app.config['LIBRARY'][l].get('unlisted', False):
            continue
        library = zoo.Library()
        library.slug = l
        library.name = app.config['LIBRARY'][l]['name']
        library.description = app.config['LIBRARY'][l]['description']
        libraries.append(library)

    return render_template('archive.html',
        description = app.config['ARCHIVE']['description'],
        title = app.config['ARCHIVE']['name'],
        breadcrumbs=_breadcrumbs(),
        archive=app.config['ARCHIVE']['name'],
        license=app.config['ARCHIVE']['license'],
        libraries=libraries,
        db=zoo.cfg.database,
    )

# @app.route('/about')
# def about():
#
#     slug = app.config['ARCHIVE']['home']['path']
#     zoo.cfg.database = _db(slug)
#     i = zoo.load(app.config['ARCHIVE']['home']['item'])
#     content = i.note
#     m = re.search(r'<h1>(.*?)</h1>', content)
#     if m:
#         i.title = m.group(1)
#     content = re.sub(r'data-attachment-key="(.*?)"',
#         'src="/{}/items/\g<1>/file"'.format(slug), content)
#     content = _process_citations(content)
#
#     return render_template('page.html',
#         title = i.title or 'Home page',
#         breadcrumbs=_breadcrumbs(),
#         archive=app.config['ARCHIVE']['name'],
#         license=app.config['ARCHIVE']['license'],
#         content=content,
#         db=zoo.cfg.database,
#         slug=slug
#     )


@app.route("/<slug>/query/<p>")
def query_predicate(slug, p):
    db = _db(slug)
    zoo.cfg.database = db
    values = zoo._get_attrs(p)
    return render_template('query_list.html',
            title='Query: {}'.format(p),
            archive=app.config['ARCHIVE']['name'],
            license=app.config['ARCHIVE']['license'],
            db=zoo.cfg.database,
            breadcrumbs=_breadcrumbs(slug),
            index=values,
            predicate=p,
            slug=slug
        )

@app.route("/<slug>/query/<p>/<o>")
def query_predicate_object(slug, p, o):
    db = _db(slug)
    zoo.cfg.database = db
    values = []
    for item in zoo._query(p, o):
        i = zoo.load(item)

        if getattr(i, 'itemType') == 'note':
            i.title = 'Note'
            m = re.search(r'<h1>(.*?)</h1>', i.note)
            if m:
                i.title = m.group(1)
                i.note = i.note.replace(i.title, '')
        parent = zoo._get_attr(i.key, 'parentItem')
        if parent:
            i.parentItem = zoo._get_attr(parent, 'title')
        values.append(i)
    return render_template('query.html',
            title='Query: {} = {}'.format(p, o),
            archive=app.config['ARCHIVE']['name'],
            license=app.config['ARCHIVE']['license'],
            db=zoo.cfg.database,
            breadcrumbs=_breadcrumbs(slug),
            items=values,
            slug=slug
        )

@app.errorhandler(404)
def page_not_found(e):
    msg = 'Page not found'
    return render_template('error.html',
        title='404',
        archive=app.config['ARCHIVE']['name'],
        license=app.config['ARCHIVE']['license'],
        breadcrumbs=_breadcrumbs(),
        content=msg), 404

@app.errorhandler(403)
def unauthorized(e):
    msg = 'Not authorized.'
    return render_template('error.html',
        title='403',
        archive=app.config['ARCHIVE']['name'],
        license=app.config['ARCHIVE']['license'],
        breadcrumbs=_breadcrumbs(),
        content=msg), 403

@app.route("/<slug>/")
def index(slug):
    db = _db(slug)
    return redirect(url_for('collections', db=db))

@app.route("/<slug>/collections/")
@cache.cached()
def collections(slug):
    db = _db(slug)
    zoo.cfg.database = db
    try:
        collections = [zoo.load(c) for c in zoo._get_collections()]
    except:
        abort(404)
    library = zoo.load_library_data()
    if library:
        metadata = library.html()
        title = library.name
    else:
        metadata = ''
        title = 'library'
    return render_template('collections.html',
            metadata=metadata,
            collections=collections,
            title=title,
            archive=app.config['ARCHIVE']['name'],
            license=app.config['ARCHIVE']['license'],
            db=zoo.cfg.database,
            breadcrumbs=_breadcrumbs(slug),
            slug=slug
        )


@app.route("/<slug>/collections/<key>")
@cache.cached(query_string=True)
def collection(slug, key):
    """Return an html fragment representing an index entry for the item.

    For notes, the title and summary are extracted from the first matching <h1>...</h1> element and the remaining note content. This is to support Zotero-imported notes, which do not support titles or abstracts.
    """
    db = _db(slug)
    zoo.cfg.database = db
    page = request.args.get(get_page_parameter(), type=int, default=1)
    offset = int((page-1)*24)
    try:
        c = zoo.load(key)
        assert c.itemType == 'collection'
    except:
        abort(404)

    items = []
    for i in c.members(offset=offset, limit=24):
        d = zoo.load(i)
        d.thumb = _html_preview(d.thumbnail(), css_class="card-img-top")
        if getattr(d, 'itemType') == 'note':
            d.title = 'Note'
            m = re.search(r'<h1>(.*?)</h1>', d.note)
            if m:
                d.title = m.group(1)
            d.abstractNote = d.note.replace(d.title, '')

        items.append(d.__dict__)

    pagination = Pagination(page=page, total=c.members_count(), per_page=24, record_name='items', bs_version='5')
    link = ('/{}/collections/{}'.format(slug,key),
    'collection')

    return render_template('list.html',
            title=getattr(c, 'name', 'Collection'),
            archive=app.config['ARCHIVE']['name'],
            items=items,
            pagination=pagination.links,
            license=app.config['ARCHIVE']['license'],
            db=zoo.cfg.database,
            breadcrumbs=_breadcrumbs(slug, link=link),
            slug=slug
        )

def _translate_zotero_uri(uri):
    # http://zotero.org/groups/4711671/items/UJ8WGSFR
    m = re.match('^.*zotero.org/groups/(.*?)/items/(.*)', uri)
    if m:
        library = m.group(1)
        key = m.group(2)
        slug = app.config['Z2S'].get(library, None)
        if slug: # in _libraries():
            return '/{}/items/{}'.format(slug, key)
    return uri

def _process_citations(txt):
    # <span class="citation" data-citation="{"citationItems":[{"uris":["http://zotero.org/groups/4711671/items/GXPF7VK9"]},{"uris":["http://zotero.org/groups/4711671/items/UJ8WGSFR"]}],"properties":{}}"> <span class="citation-item">...</span>...</span>
    soup = BeautifulSoup(txt, 'html.parser')
    citations = soup.find_all('span', 'citation')
    for c in citations:
        data = urllib.parse.unquote(c.get('data-citation', ''))
        if not 'citationItems' in data:
            # TODO - perform more robust error checking
            continue
        j = json.loads(data)
        uris = [ i['uris'][0] for i in j['citationItems'] ]
        n = 0
        for ci in c.find_all('span', 'citation-item'):
            ci.name = 'a'
            ci['href'] = _translate_zotero_uri(uris[n])
            n = n+1
    return str(soup)

def note(slug, i):
    content = i.note
    i.title = 'Note'
    m = re.search(r'<h1>(.*?)</h1>', i.note)
    if m:
        i.title = m.group(1)
    del i.note # don't show in the metadata table
    content = re.sub(r'data-attachment-key="(.*?)"',
        'src="\g<1>/file"', content)
    content = _process_citations(content)
    link = ('/{}/items/{}'.format(slug, i.key),
    'note')
    metadata=i.html()

    return render_template('note.html',
        title = i.title,
        breadcrumbs=_breadcrumbs(slug, link=link),
        archive=app.config['ARCHIVE']['name'],
        license=app.config['ARCHIVE']['license'],
        content=content,
        metadata=metadata,
        children = [zoo.load(key) for key in i.children()],
        db=zoo.cfg.database,
        slug=slug
    )


@app.route('/<slug>/items/<key>')
@cache.cached()
def item_finding_aid(slug, key):
    """Return an html document containing a full finding aid for the item."""
    db = _db(slug)
    zoo.cfg.database = db
    try:
        i = zoo.load(key)
        assert i.key is not None
        # FIXME: more robust error checking
    except:
        abort(404)
    if getattr(i, 'collection', None):
        i.collection = [
        '<a href="../collections/{}">{}</a>'.format(
                c, zoo._get_attr(c, 'name') ) for c in i.collection ]

    if getattr(i, 'filename', None):
        i.filename = '<a href="{}/file">{}</a>'.format(
            i.key, i.filename)

    if getattr(i, 'tag', None):
        i.tag = ['<a href="/{}/query/tag/{}">{}</a>'.format(
            slug, tag, tag) for tag in i.tag]

    # FIXME: Move to a public function
    # Load a schema from the database if present, otherwise use cfg
    # value as a fallback
    schema = zoo._get_attr(db.zfill(8), 'schema')
    if schema:
        zoo.cfg.schema = schema

    if zoo.cfg.schema == 'zotero':
        if getattr(i, 'itemType', '') == 'annotation':
            # link to open annotations in Zotero
            # this must come BEFORE we convert i.parentItem to a link
            zoteroLink = 'zotero://open-pdf/groups/{}/items/{}?page={}&annotation={}'.format(
                app.config['S2Z'][slug],
                i.parentItem,
                i.annotationPageLabel,
                i.key
            )
            i.zoteroLink = '<a href="{z}">{z}</a>'.format(z=zoteroLink)
        else:
            # zotero://select/groups/4711671/items/YMP7ZISM
            zoteroLink = 'zotero://select/groups/{}/items/{}'.format(
                app.config['S2Z'][slug],
                i.key
            )
            i.zoteroLink = '<a href={z}>{z}</a>'.format(z=zoteroLink)

    if getattr(i, 'parentItem', None):
        parentItemName = zoo._get_attr(i.parentItem, 'title') or i.parentItem
        i.parentItem = '<a href="/{}/items/{}">{}</a>'.format(slug, i.parentItem, parentItemName)

    if getattr(i, 'itemType', '') == 'note':
        return note(slug, i)

    html = i.html()
    html = re.sub(r'data-attachment-key="(.*?)"',
        'src="\g<1>/file"', html)

    preview = _html_preview(i.thumbnail_preview())
    return render_template('item.html',
        title = i.title or 'Item',
        breadcrumbs=_breadcrumbs(slug, i.ancestors()),
        archive=app.config['ARCHIVE']['name'],
        preview=preview,
        metadata = html,
        children = [zoo.load(key) for key in i.children()],
        license=app.config['ARCHIVE']['license'],
        db=zoo.cfg.database,
        slug=slug
    )

@app.route('/login')
@auth.login_required()
def login():
    user = auth.current_user()
    return("Successfully logged in.")

@app.route('/logout')
def logout():
    response = 'Logging out.'
    return response, 401

@app.route('/<slug>/items/<key>/file')
@auth.login_required(optional=True)
def file(slug, key):
    db = _db(slug)
    user = auth.current_user()
    if not user in app.config['LIBRARY'][slug]['users']:
        abort(403) # login required
    zoo.cfg.database = db
    i = zoo.load(key)
    file_data = i.file()
    if not file_data:
        return "No file available"
    if i.contentType == 'text/html':
        # zotero html attachments are actually stored as zip files
        i.contentType = 'application/zip'
        i.filename = '{}.zip'.format(key)
    return send_file(
        io.BytesIO(file_data),
        mimetype=i.contentType,
        as_attachment=False,
        download_name=i.filename)
