#!/usr/bin/python3

"""Z3 sync

    This utility synchronizes Zotero group libraries with a local sqlite database consisting of semantic triples.

    See also the utilities ZOO (zoo.py), which provides an object-oriented API for Z3 databases, and the associated web app (app.py).

    Copyright 2022, Eric Thrift

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

from dateutil.parser import parse as dateparse
import argparse
import json
import jsonpatch
from pyzotero import zotero, zotero_errors
import sqlite3

## WORKFLOW: Based on guidelines at
## https://www.zotero.org/support/dev/web_api/v3/syncing


def get_itemType(con, itemKey):
    """Return the itemType of an item in the database."""
    c = con.execute("select object from metadata where subject = ? and predicate = 'itemType'", (itemKey,))
    itemType = c.fetchone()[0]
    return itemType

def rdf2dict(con, args, itemKey):
    """Create a dictionary out of the metadata in the sqlite database."""
    data = {}
    creatorTypes = ["artist", "attorneyAgent", "author", "bookAuthor",
        "cartographer", "castMember", "commenter", "composer",
        "contributor", "cosponsor", "counsel", "director", "editor",
        "guest", "interviewee", "interviewer", "inventor", "performer",
        "podcaster", "presenter", "producer", "programmer", "recipient",
        "reviewedAuthor", "scriptwriter", "seriesEditor", "sponsor",
        "translator", "wordsBy" ]

    fields = ["abstractNote", "accessDate", "applicationNumber",
        "archive", "archiveID", "archiveLocation", "artworkMedium",
        "artworkSize", "assignee", "audioFileType",
        "audioRecordingFormat", "billNumber", "blogTitle", "bookTitle",
        "callNumber", "caseName", "citationKey", "code", "codeNumber",
        "codePages", "codeVolume", "committee", "company",
        "conferenceName", "country", "court", "date", "dateAdded",
        "dateDecided", "dateEnacted", "dateModified", "dictionaryTitle",
        "distributor", "docketNumber", "documentNumber", "DOI",
        "edition", "encyclopediaTitle", "episodeNumber", "extra",
        "filingDate", "firstPage", "forumTitle", "genre", "history",
        "institution", "interviewMedium", "ISBN", "ISSN", "issue",
        "issueDate", "issuingAuthority", "itemType",
        "journalAbbreviation", "label", "language", "legalStatus",
        "legislativeBody", "letterType", "libraryCatalog",
        "manuscriptType", "mapType", "meetingName", "nameOfAct",
        "network", "number", "numberOfVolumes", "numPages", "pages",
        "patentNumber", "place", "postType", "presentationType",
        "priorityNumbers", "proceedingsTitle", "programmingLanguage",
        "programTitle", "publicationTitle", "publicLawNumber",
        "publisher", "references", "reporter", "reporterVolume",
        "reportNumber", "reportType", "repository", "rights",
        "runningTime", "scale", "section", "series", "seriesNumber",
        "seriesText", "seriesTitle", "session", "shortTitle", "studio",
        "subject", "system", "thesisType", "title", "university", "url",
        "versionNumber", "videoRecordingFormat", "volume",
        "websiteTitle", "websiteType",
        # additional data fields returned by Zotero API
        'key', 'version', 'parentItem',
        "annotationType", "annotationAuthorName", "annotationText",
        "annotationComment", "annotationColor", "annotationPageLabel",
        "annotationSortIndex", "annotationPosition", "note"
        ]

    for row in con.execute("select * from metadata where subject = ?", (itemKey, )):
        # process creators
        if row[1] in creatorTypes:
            if not 'creators' in data:
                data['creators'] = []
            if ',' in row[2]: # lastname, firstname
                lastName, sep, firstName = row[2].partition(', ')
                data['creators'].append( {
                    'creatorType': row[1],
                    'lastName': lastName,
                    'firstName': firstName
                    } )
            else:
                data['creators'].append( {
                    'creatorType': row[1],
                    'name': row[2],
                    } )
        elif row[1] == 'tag':
            if not 'tags' in data:
                data['tags'] = []
            data['tags'].append({'tag': row[2]})
        elif row[1] == 'collection':
            if not 'collections' in data:
                data['collections'] = []
            data['collections'].append(row[2])
        elif row[1] in fields:
            data[row[1]] = row[2]

    if not 'version' in data.keys():
        data['version'] = 0
    if data.get('itemType', None) == 'collection':
        # delete from data to upload to server
        del(data['itemType'])

    return data


def dict2rdf(data):
    """Convert a dictionary to RDF triples."""
    itemKey = data.get('key', None)
    if not itemKey:
        return []
    out = []

    # process the creator fields
    for k, v in data.items():
        if k == 'creators':
            for creator in v: # list of dicts
                if creator.get('name', None): # single name field
                    out.append((itemKey, creator['creatorType'], creator['name']))
                else: # has lastName and firstName fields
                    out.append((itemKey, creator['creatorType'],
                        '{}, {}'.format( creator.get('lastName', ''),
                            creator.get('firstName', '')
                            )))
        elif k == 'tags': # list of {'tag': tagName} dicts
            out.extend([(itemKey, 'tag', t['tag']) for t in v])
        elif k == 'collections': # list of itemKeys
            out.extend([(itemKey, 'collection', i) for i in v])
        elif k in ('relations'):
            # these are Zotero URIs: dc:replaces and dc:relation
            # {"dc:relation": ["http://zotero.org/groups/4711671/items/JAWVSH9G", ...]}
            # we have a list of URIs or single string if ther is just one
            # TODO:
            # extract the group ID and the key
            # create a link to the appropriate DB for the group library. We have the library keys inside the databases, but we will also need an external mapping of some kind.
            pass
        else:
            out.append((itemKey, k, v))

    # store the pristine json for diff purposes
    out.append((itemKey, '.zotero-sync-data', json.dumps(data)))
    return out



def set_synced(con, itemKeys):
    """set .zotero-sync-status of given itemKeys to "synced"""

    c = con.execute("select * from metadata where predicate = '.zotero-sync-status'")
    for itemKey in itemKeys:
        con.execute("update metadata set subject = :key, predicate = '.zotero-sync-status', object = 'synced' where subject = :key and predicate = '.zotero-sync-status' limit 1", {'key': itemKey})
        con.commit()
    return

def get_updated_local_objects(con, args):
    """Retrieve the versions of all objects changed since the last check """
    # NB. We also need a function to check for items without a sync status flag
    items = {'new': [], 'modified': []}
    collections = {'new': [], 'modified': []}

    c = con.execute("select subject, predicate, object from metadata where predicate = '.zotero-sync-status'")
    for row in c:
        if not row[2] in ('new', 'modified'):
            continue
        itemType = get_itemType(con, row[0])
        if itemType == 'collection':
            collections[row[2]].append(row[0])
        elif itemType == 'library':
            continue # FIXME: support later
        else:
            items[row[2]].append(row[0])

    return items, collections


def get_updated_remote_objects(con, args):
    """Retrieve the versions of all objects changed since the last check for that object type, using the appropriate request for each object type"""

    zot = zotero.Zotero(args.library_id, 'group', args.api_key)
    updated_items = zot.item_versions(since=args.version)
    updated_collections = zot.collection_versions(since=args.version)

    # For each returned object, compare the version to the local version of the
    # object. If the remote version doesn't match, queue the object for
    # download.

    new_items = retrieve_remote(con, updated_items)
    new_collections = retrieve_remote(con, updated_collections, collection=True)
    for c in new_collections:
        c['data']['itemType'] = 'collection'

    return new_items + new_collections


def retrieve_remote(con, updated_objects, collection=False):
    """Fetch data for updated objects from the Zotero server."""

    zot = zotero.Zotero(args.library_id, 'group', args.api_key)
    queue = []
    for key, remote_version in updated_objects.items():
        c = con.execute("select object from metadata where subject = ? and predicate = ? ", (key, "version"))
        local_version = c.fetchone()
        # returns None if not present in the local database
        if not local_version:
            queue.append(key)
        elif int(local_version[0]) < int(remote_version):
            queue.append(key)

    # Retrieve the queued objects, as well as any flagged as having previously
    # failed to save, by key, up to 50 at a time, using the appropriate request
    # for each object type
    new_objects = []
    # Break request into sets of 50 to avoid error 414 Request-URI Too Long
    for i in range(0, len(queue), 50):
        slice = queue[i:i + 50]
        if collection:
            new_objects.extend(zot.collections(itemKey=','.join(slice)))
        else:
            new_objects.extend(zot.items(itemKey=','.join(slice)))
    return new_objects

def process_remote_changes(con, args, data):
    """Update the local database with input data from the server."""

    c = con.execute("select object from metadata where subject = ? and predicate = ?", (data['key'], '.zotero-sync-status' ))
    synced = c.fetchone()

    if not synced or synced[0] == 'synced':
        # item doesn't exist locally OR no local changes
        # create local object with version = Last-Modified-Version and set
        # synced = 'synced'
        if synced:
            # delete the out-of-date local data first
            print("Updating {}".format(data['key']))
            con.execute(
                "delete from metadata where subject = ? and typeof(object) != 'blob'", (data['key'], ) )

        rdf = dict2rdf(data)
        rdf.append((data['key'], '.zotero-sync-status', 'synced' ))

        con.executemany("insert into metadata(subject, predicate, object) values (?, ?, ?)", rdf)
        con.commit()

    else:
        # perform conflict resolution
        if args.merge_priority == 'remote':
            # merge remote changes into local (overwrite)
            local = rdf2dict(con, args, data['key'])
            # This should keep local data not modified on the server.
            # But the push will fail silently if we have fields
            # that are unrecognized by zotero.
            patch = jsonpatch.make_patch(local, data)
            new_data = patch.apply(local, patch)
            rdf = dict2rdf(new_data)
            rdf.append((data['key'], '.zotero-sync-status', 'synced' ))
            con.execute("delete from metadata where subject = ? and typeof(object) != 'blob'",
                (data['key'],))
            con.executemany("insert into metadata(subject, predicate, object) values (?, ?, ?)", rdf)
            con.commit()

        elif args.merge_priority == 'local':
            # set .zotero-sync-status to "modified" and restart the sync
            con.execute("update metadata set subject = :key, predicate = '.zotero-sync-status', object = 'modified' where subject = :key and predicate = '.zotero-sync-status' limit 1", {key: data['key']})
            con.commit()

        elif args.merge_priority == 'ignore':
            # defer / perform manual changes later
            # set to "conflict"
            con.execute("update metadata set subject = :key, predicate = '.zotero-sync-status', object = 'conflict' where subject = :key and predicate = '.zotero-sync-status' limit 1", {key: data['key']})
            con.commit()



def process_local_changes(con, args):
    """Identify local changes and update to the server."""
    items, collections = get_updated_local_objects(con, args)
    # retrieve the json content for each object
    new_items = [rdf2dict(con, args, o) for o in items['new']]
    modified_items = [rdf2dict(con, args, o) for o in items['modified']]
    new_collections = [rdf2dict(con, args, o) for o in collections['new']]
    modified_collections = [rdf2dict(con, args, o) for o in collections['modified']]

    # upload everything via api, including last known version
    # TODO: Address silent errors with unknown fields
    zot = zotero.Zotero(args.library_id, 'group', args.api_key)
    if new_items:
        r = zot.create_items(new_items)
        if r:
            set_synced(con, items['new'])
    if modified_items:
        r = zot.update_items(modified_items)
        if r:
            set_synced(con, items['modified'])
    if new_collections:
        r = zot.create_collections(new_collections)
        if r:
            set_synced(con, collections['new'])
    if modified_collections:
        r = zot.update_collections(modified_collections)
        if r:
            set_synced(con, collections['modified'])
    return

def download_new_remote_files(con, args):
    """Download newly added attachment files from the Zotero server."""
    # This function loads the file contents into memory, so we are
    # assuming that the Zotero library doesn't contain huge files.
    # Sqlite will not accept blobs larger than 2GB, and the default
    # maximum is 1 GB (1 billion bytes)
    # FIXME: Use local filesystem storage instead

    # find objects with itemType "attachment" that do not have
    # a <file> metadata field
    c = con.execute("select subject from metadata where predicate = 'file'")
    attachments_with_files = [row[0] for row in c]
    c = con.execute("select distinct subject from metadata where predicate = 'linkMode' and object = 'imported_file' or object = 'imported_url' or object = 'embedded_image'")
    attachments = [row[0] for row in c]
    missing_files = [a for a in attachments if not a in attachments_with_files]

    zot = zotero.Zotero(args.library_id, 'group', args.api_key)
    for itemKey in missing_files:
        print("Downloading {}...".format(itemKey))

        try:
            blob = zot.file(itemKey)
        except zotero_errors.ResourceNotFound as e:
            print(e)
            continue
        c = con.execute("insert into metadata(subject, predicate, object) values (?, ?, ?)", (itemKey, 'file', sqlite3.Binary(blob)))
        con.commit()

def upload_new_local_files(args):
    """Not yet implemented."""
    # local files are saved as <itemKey> file <blob>
    # pyzotero expects them to be files on disk with the same name as
    # in the metadata, so we will need to extract them to a temporary
    # file first
    # (1a) check that the .zotero-sync-status flag does not indicate this is a local-only file
    # (1b) check that file exists (non-linked) and is < maximum size
    # (2) obtain metadata dict
    # (3) extract blob to disk using stored filename (or abort)
    # (4) upload to Zotero
    # (5) print results to log
    # data is a list of data dicts:
    # zot.upload_attachments(data)
    pass

def process_remote_deletions(con, args):
    """Remove items that have been deleted remotely."""
    # LIMITATIONS: The API does not actually return a list of
    # permanently deleted files or standalone notes, so we
    # will have trouble deleting them from the local library
    zot = zotero.Zotero(args.library_id, 'group', args.api_key)
    deleted = zot.deleted(since=args.version)
    # returns dict with lists of keys
    for k in deleted['items'] + deleted['collections']:
        con.execute("delete from metadata where subject = ?", (k,))
        con.commit()

def process_local_deletions(args):
    """Not yet implemented."""
    pass

def get_highest_version(con, args):
    """ Get the most recent version number from the database."""
    c = con.execute("select object from metadata where predicate = 'version' order by object desc")
    ver = c.fetchone()
    if ver:
        return int(ver[0])
    else:
        return 0

class Z(zotero.Zotero):
    """Local version of pyzotero Zotero class with additional functions"""

    def group(self, **kwargs):
        """Get group data. This is not currently supported in pyzotero."""
        query_string = "/groups/{u}"
        return self._build_query(query_string)

def get_library_data(con, args):
    """Retrieve the remote metadata for the group library."""
    # this is not wrapped by pyzotero
    # https://api.zotero.org/groups/{library_id}/
    # data['name'], data['description']
    zot = Z(args.library_id, 'group', args.api_key)
    try:
        r = zot._retrieve_data(zot.group()).json()
    except zotero_errors.UserNotAuthorised as e:
        print(e)
        return None
    data = r.get('data', None)
    if not data:
        return
    key = args.library_id.zfill(8)

    vals = [
        (key, 'key', str(args.library_id)),
        (key, 'itemType', 'library'),
        (key, 'name', data['name']),
        (key, 'description', data['description']),
        (key, 'url', data['url']),
        #TODO: Test uploading locally edited data
        (key, 'version', data['version'])
        ]

    # No need to delete existing values first --
    # we don't have multiple values for
    # a single key, so we can substitute modified values for
    # existing ones, rather than appending
    # This also allows us to use custom fields

    con.executemany("insert or replace into metadata(subject, predicate, object) values (?, ?, ?)", vals)
    con.commit()


def run(args):
    con = sqlite3.connect(args.database)
    con.execute("create table if not exists metadata(subject text not null check (length(subject)=8), predicate, object)")
    con.execute("CREATE INDEX if not exists 'items' ON 'metadata' ( 'subject' )")
    con.execute("CREATE INDEX if not exists 'predicates-objects' ON 'metadata' ( 'predicate', 'object' ) where typeof(object) != 'blob'")
    con.execute("CREATE INDEX if not exists 'subjects' ON 'metadata' ( 'subject', 'predicate' )")
    con.execute("CREATE INDEX if not exists 'predicates' ON 'metadata' ( 'predicate' )")

    con.commit()
    if args.get_library_data:
        data = get_library_data(con, args)
    if not args.version:
        args.version = get_highest_version(con, args)
    if args.remove_deleted:
        process_remote_deletions(con, args)
    new_remote_objects = get_updated_remote_objects(con, args)
    for item in new_remote_objects:
        process_remote_changes(con, args, item['data'])
    if args.download_files:
        download_new_remote_files(con, args)
    process_local_changes(con, args)
    con.close()

if __name__ == '__main__':
    # Initialize the command line interface
    parser = argparse.ArgumentParser(description='Sync with Zotero library')
    parser.add_argument('--api-key',  required=False,
            help='API key (not required for read-only access)')
    parser.add_argument('--library-id', required=True)
    parser.add_argument('--version', type=int)
    parser.add_argument('--get-library-data', action='store_true',
        help="Download library name and description from the Zotero server. This clears and replaces local library-level metadata.")
    parser.add_argument('--database', default='z3.db')
    parser.add_argument('--download-files', action='store_true')
    parser.add_argument('--remove-deleted', action='store_true')
    parser.add_argument('--merge-priority',
        choices=['remote', 'local', 'ignore'],
        default='remote',
        help='conflict resolution strategy. Remote: keep remote values, '
             'overwriting local data. Local: keep local values. '
             'Ignore: skip items with conflicting data. '
             'Fields that are available in only one version are not deleted.')
    args = parser.parse_args()
    run(args)
