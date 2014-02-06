"""Interface and schema for DB access
"""

__author__ = 'tunnell'

import logging
import pickle
import gzip
import numpy as np
import inspect

import pymongo
import snappy
import mongomock
import os

CONNECTIONS = {}

def mock_get_mongo_db_objects(a='127.0.0.1'):
    print("Using mock")

    # script directory
    dir = os.path.dirname(
        os.path.abspath(inspect.getfile(inspect.currentframe())))

    file = gzip.open(os.path.join(dir, 'data.p'), 'rb')
    c = mongomock.Connection()
    db = c.db
    collection = db.collection

    for doc in pickle.load(file):
        if 'module' not in doc:
            print(doc)
        collection.insert(doc)

    file.close()

    return c, db, collection


def get_server_name():
    """Get the current server name

    .. todo:: should this be a 'config' item or somethign?
    :returns: str -- server address
    """
    return "127.0.0.1"


def get_sort_key():
    """Sort key used for MongoDB sorting and indexing.

    Args:
       None

    Returns:
       list:  Returns, per pymongo format, a list of (variable, order) pairs.

    """
    return [('triggertime', pymongo.DESCENDING),
            ('module', pymongo.DESCENDING),
            ('_id', pymongo.DESCENDING)]


def get_mongo_db_objects(server=get_server_name(), selection='input'):
    """This function returns pymongo objects

    This does all the connection setup, that is specific to this program.

    :param selection: 'input' if caen blocks, otherwise 'output' for output
    :type selection: str.
    :param server: The server IP or DNS name MongoDB.
    :type server: str.
    :returns:  list -- [Connection, Database, Collection]
    :raises: pymongo.errors.PyMongoError
    """
    if selection in CONNECTIONS.keys():
        return CONNECTIONS[selection]

    if selection == 'input':
        db_name = 'data'            # todo: config?
        collection_name = 'XENON100'  # todo: config?
    elif selection == 'output':
        db_name = 'output'
        collection_name = 'somerun'
    else:
        raise ValueError()

    c = pymongo.MongoClient(server)

    db = c[db_name]
    collection = db[collection_name]

    if selection == 'input':
        num_docs_in_collection = collection.count()
        if num_docs_in_collection == 0:
            raise RuntimeError("Collection %s.%s contains no events; can't continue" %
                                (db_name, collection_name))

        collection.ensure_index(get_sort_key(),
                                background=True)
    CONNECTIONS[selection] = (c, db, collection)
    return c, db, collection


def get_pymongo_collection(selection='input'):
    """Returns the current pymongo collection.

    selection is 'input' for input Caen blocks, 'output' for trigger events
    :returns:  Collection -- Mongo collection with docs
    """
    return get_mongo_db_objects(selection)[2]


def get_min_time(collection):
    """Get minimum time in collection.

    Args:
       collection (Collection):  A pymongo Collection that will be queried

    Returns:
       int:  A time in units of 10 ns

    """

    # See bug #6. https://github.com/tunnell/cito/issues/6
    if isinstance(collection, mongomock.collection.Collection):
        my_min = None

        for doc in collection.find():
            if my_min is None or doc['triggertime'] < my_min:
                my_min = doc['triggertime']
        if my_min is None:
            raise RuntimeError("Can't find min time in mock")
        return my_min

    sort_key = get_sort_key()
    sort_key = [(x[0], pymongo.ASCENDING) for x in sort_key]

    cursor = collection.find({},
                             fields=['triggertime'],
                             limit=1,
                             sort=sort_key)

    doc = next(cursor)
    logging.error('trig time: %s' % str(doc))
    time = doc['triggertime']
    if time == None:
        raise ValueError("No time found when searching for minimal time")
    logging.debug("Minimum time: %d", time)
    return time


def get_max_time(collection, min_time=0):
    """Get maximum time that has been seen by all boards.

    Args:
       collection (Collection):  A pymongo Collection that will be queried
       min_time (int): Time that the max must be larger than

    Returns:
       int:  A time in units of 10 ns

    """
    sort_key = get_sort_key()
    modules = collection.distinct('module')

    times = {}

    if not modules:
        raise RuntimeError("No data for any module found")

    for module in modules:
        query = {'module': module,
                 'triggertime': {'$gt': min_time}}

        cursor = collection.find(query,
                                 fields=['triggertime', 'module'],
                                 limit=1,
                                 sort=sort_key)

        # assert(cursor.explain()['indexOnly'])

        times[module] = next(cursor)['triggertime']

    # Want the earliest time (i.e., the min) of all the max times
    # for the boards.
    time = min(times.values())

    return time


def get_data_from_doc(doc):
    """From a mongo document, fetch the data payload and decompress if
    necessary

    Args:
       doc (dictionary):  Document from mongodb to analyze

    Returns:
       bytes: decompressed data

    """
    data = doc['data']
    assert (len(data) != 0)

    if doc['zipped']:
        data = snappy.uncompress(data)

    data = np.fromstring(data,
                        dtype=np.uint32)

    if len(data) == 0:
        raise IndexError("Data has zero length")

    return data


def get_data_docs(t0, t1):
    """Fetch from DB the documents within time range.

    .. todo:: Must this know padding?  Maybe just hand cursor so can mock?

    :param t0: Initial time to query.
    :type t0: int.
    :param t1: Final time.
    :type t1: int.
    :returns:  list -- Input documents see docs :ref:`data_format#input`
    :raises: AssertionError
    """
    conn, mongo_db_obj, collection = get_mongo_db_objects()

    # $gte and $lt are special mongo functions for greater than and less than
    subset_query = {"triggertime": {'$gte': t0,
                                    '$lt': t1}}

    return list(collection.find(subset_query))
