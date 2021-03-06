# -*- coding: utf-8 -*-
#This file is part of Tryton & Nereid. The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
import os
import posixpath
import mimetypes
from time import time
from zlib import adler32
import re
import urllib
import unicodedata
from functools import wraps
from hashlib import md5

from flask.helpers import (_PackageBoundObject, locked_cached_property,  # noqa
        get_flashed_messages, flash)
from werkzeug import Headers, wrap_file, redirect, abort
from werkzeug.exceptions import NotFound

from .globals import session, _request_ctx_stack, current_app, request


_SLUGIFY_STRIP_RE = re.compile(r'[^\w\s-]')
_SLUGIFY_HYPHENATE_RE = re.compile(r'[-\s]+')


def url_for(endpoint, **values):
    """
    Generates a URL to the given endpoint with the method provided.
    The endpoint is relative to the active module if modules are in use.

    Here are some examples:

    ==================== ======================= =============================
    Active Module        Target Endpoint         Target Function
    ==================== ======================= =============================
    `None`               ``'index'``             `index` of the application
    `None`               ``'.index'``            `index` of the application
    ``'admin'``          ``'index'``             `index` of the `admin` module
    any                  ``'.index'``            `index` of the application
    any                  ``'admin.index'``       `index` of the `admin` module
    ==================== ======================= =============================

    Variable arguments that are unknown to the target endpoint are appended
    to the generated URL as query arguments.

    For more information, head over to the :ref:`Quickstart <url-building>`.

    :param endpoint: the endpoint of the URL (name of the function)
    :param values: the variable arguments of the URL rule
    :param _external: if set to `True`, an absolute URL is generated.
    :param _secure: if set to `True`, returns an absolute https URL.
    :param _params: if specified as list of tuples or dict, additional url
                    parameters. If params is specified then the default
                    behaviour of appending_unknown paramters to URL generated
                    is disabled.
    """
    ctx = _request_ctx_stack.top
    external = values.pop('_external', False)

    secure = values.pop('_secure', False)
    if secure:
        # A secure url can only be generated on an external url
        external = True

    params = values.pop('_params', None)

    if 'language' not in values:
        values['language'] = request.nereid_language.code
    rv = ctx.url_adapter.build(
        endpoint, values, force_external=external, append_unknown=not params)
    if params:
        params = [(k, v.encode('UTF-8')) for k, v in params]
        rv = u'%s?%s' % (rv, urllib.urlencode(params))
    return (rv.replace('http://', 'https://') if secure else rv)


def secure(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not request.is_secure:
            return redirect(request.url.replace('http://', 'https://'))
        else:
            return function(*args, **kwargs)
    return decorated_function


def login_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('nereid.website.login', next=request.url))
        return function(*args, **kwargs)
    return decorated_function


class permissions_required(object):
    """
    Decorator helper to check if the specified permissions
    rest with the user
    """
    def __init__(self, perm_all=None, perm_any=None):
        self.perm_all = frozenset(perm_all if perm_all else [])
        self.perm_any = frozenset(perm_any if perm_any else [])

    def __call__(self, function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            if request.nereid_user.has_permissions(
                    self.perm_all, self.perm_any
            ):
                return function(*args, **kwargs)
            abort(403)
        return wrapper


def send_from_directory(directory, filename, **options):
    """
    Send a file from a given directory with :func:`send_file`.  This
    is a secure way to quickly expose static files from an upload folder
    or something similar.

    Example usage::

        def download_file(self, filename):
            return send_from_directory(app.config['UPLOAD_FOLDER'],
                                       filename, as_attachment=True)

    .. admonition:: Sending files and Performance

       It is strongly recommended to activate either `X-Sendfile` support in
       your webserver or (if no authentication happens) to tell the webserver
       to serve files for the given path on its own without calling into the
       web application for improved performance.

    :param directory: the directory where all the files are stored.
    :param filename: the filename relative to that directory to
                     download.
    :param options: optional keyword arguments that are directly
                    forwarded to :func:`send_file`.
    """
    filename = posixpath.normpath(filename)
    if filename.startswith(('/', '../')):
        raise NotFound()
    filename = os.path.join(directory, filename)
    if not os.path.isfile(filename):
        raise NotFound()
    return send_file(filename, conditional=True, **options)


def send_file(filename_or_fp, mimetype=None, as_attachment=False,
              attachment_filename=None, add_etags=True,
              cache_timeout=60 * 60 * 12, conditional=False):
    """
    Sends the contents of a file to the client.  This will use the
    most efficient method available and configured.  By default it will
    try to use the WSGI server's file_wrapper support.  Alternatively
    you can set the application's :attr:`~Flask.use_x_sendfile` attribute
    to ``True`` to directly emit an `X-Sendfile` header.  This however
    requires support of the underlying webserver for `X-Sendfile`.

    By default it will try to guess the mimetype for you, but you can
    also explicitly provide one.  For extra security you probably want
    to sent certain files as attachment (HTML for instance).  The mimetype
    guessing requires a `filename` or an `attachment_filename` to be
    provided.

    Please never pass filenames to this function from user sources without
    checking them first.  Something like this is usually sufficient to
    avoid security problems::

        if '..' in filename or filename.startswith('/'):
            abort(404)

    :param filename_or_fp: the filename of the file to send.  This is
                           relative to the :attr:`~nereid.root_path` if a
                           relative path is specified.
                           Alternatively a file object might be provided
                           in which case `X-Sendfile` might not work and
                           fall back to the traditional method.  Make sure
                           that the file pointer is positioned at the start
                           of data to send before calling :func:`send_file`.
    :param mimetype: the mimetype of the file if provided, otherwise
                     auto detection happens.
    :param as_attachment: set to `True` if you want to send this file with
                          a ``Content-Disposition: attachment`` header.
    :param attachment_filename: the filename for the attachment if it
                                differs from the file's filename.
    :param add_etags: set to `False` to disable attaching of etags.
    :param conditional: set to `True` to enable conditional responses.
    :param cache_timeout: the timeout in seconds for the headers.
    """
    mtime = None
    if isinstance(filename_or_fp, basestring):
        filename = filename_or_fp
        file = None
    else:
        from warnings import warn
        file = filename_or_fp
        filename = getattr(file, 'name', None)

        # XXX: this behaviour is now deprecated because it was unreliable.
        # removed in Flask 1.0
        if not attachment_filename and not mimetype and \
                isinstance(filename, basestring):
            warn(
                DeprecationWarning(
                    'The filename support for file objects passed to '
                    'send_file is not deprecated. Pass an attach_filename '
                    'if you want mimetypes to be guessed.'
                ), stacklevel=2
            )
        if add_etags:
            warn(
                DeprecationWarning(
                    'In future flask releases etags will no longer be '
                    'generated for file objects passed to the send_file '
                    'function because this behaviour was unreliable. Pass '
                    'filenames instead if possible, otherwise attach an etag '
                    'yourself based on another value'
                ), stacklevel=2
            )

    if filename is not None:
        if not os.path.isabs(filename):
            from trytond.config import CONFIG
            filename = os.path.join(
                CONFIG['data_path'],
                current_app.database_name,
                filename)
    if mimetype is None and (filename or attachment_filename):
        mimetype = mimetypes.guess_type(filename or attachment_filename)[0]
    if mimetype is None:
        mimetype = 'application/octet-stream'

    headers = Headers()
    if as_attachment:
        if attachment_filename is None:
            if filename is None:
                raise TypeError('filename unavailable, required for '
                                'sending as attachment')
            attachment_filename = os.path.basename(filename)
        headers.add('Content-Disposition', 'attachment',
                    filename=attachment_filename)

    if current_app.use_x_sendfile and filename:
        if file is not None:
            file.close()
        headers['X-Sendfile'] = filename
        data = None
    else:
        if file is None:
            file = open(filename, 'rb')
            mtime = os.path.getmtime(filename)
        data = wrap_file(request.environ, file)

    rv = current_app.response_class(data, mimetype=mimetype, headers=headers,
                                    direct_passthrough=True)

    # if we know the file modification date, we can store it as the
    # current time to better support conditional requests.  Werkzeug
    # as of 0.6.1 will override this value however in the conditional
    # response with the current time.  This will be fixed in Werkzeug
    # with a new release, however many WSGI servers will still emit
    # a separate date header.
    if mtime is not None:
        rv.date = int(mtime)

    rv.cache_control.public = True
    if cache_timeout:
        rv.cache_control.max_age = cache_timeout
        rv.expires = int(time() + cache_timeout)

    if add_etags and filename is not None:
        rv.set_etag('nereid-%s-%s-%s' % (
            os.path.getmtime(filename),
            os.path.getsize(filename),
            adler32(filename) & 0xffffffff
        ))
        if conditional:
            rv = rv.make_conditional(request)
            # make sure we don't send x-sendfile for servers that
            # ignore the 304 status code for x-sendfile.
            if rv.status_code == 304:
                rv.headers.pop('x-sendfile', None)
    return rv


def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.

    From Django's "django/template/defaultfilters.py".

    Source : http://code.activestate.com/recipes/577257/ (r2)

    >>> slugify('Sharoon Thomas')
    u'sharoon-thomas'
    >>> slugify('Sharoon Thomås')
    u'sharoon-thoms'
    >>> slugify(u'Sharoon Thomås')
    u'sharoon-thomas'
    """
    if not isinstance(value, unicode):
        value = unicode(value, errors='ignore')
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore')
    value = unicode(_SLUGIFY_STRIP_RE.sub('', value).strip().lower())
    return _SLUGIFY_HYPHENATE_RE.sub('-', value)


def _rst_to_html_filter(value):
    """
    Converts RST text to HTML
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    This uses docutils, if the library is missing, then the
    original text is returned

    Loading to environment::
             from jinja2 import Environment
             env = Environment()
             env.filters['rst'] = rst_to_html
             template = env.from_string("Welcome {{name|rst}}")
             template.render(name="**Sharoon**")
    """
    try:
        from docutils import core
        parts = core.publish_parts(source=value, writer_name='html')
        return parts['body_pre_docinfo'] + parts['fragment']
    except Exception:
        return value


def key_from_list(list_of_args):
    """
    Builds a key from a list of arguments which could be used for caching
    The key s constructed as an md5 hash
    """
    hash = md5()
    hash.update(repr(list_of_args))
    return hash.hexdigest()


def get_website_from_host(http_host):
    """Try to find the website name from the HTTP_HOST name"""
    return http_host.split(':')[0]


def make_crumbs(browse_record, endpoint, add_home=True, max_depth=10,
                field_map_changes=None, root_ids=None):
    """
    Makes bread crumbs for a given browse record based on the field
    parent of the browse record

    :param browse_record: The browse record of the object from which upward
                          tracing of crumbs need to be done
    :param endpoint: The endpoint against which the urls have to be generated
    :param add_home: If provided will add home and home url as the first item
    :param max_depth: Maximum depth of the crumbs
    :param field_map_changes: A dictionary/list of key value pair (tuples) to
                              update the default field_map. Only the changing
                              entries need to be provided.
    :param root_ids: IDs of root nodes where the recursion to a parent node
                     will need to be stopped. If not specified the recursion
                     continues upto the max_depth. Expects a list or tuple of
                     ids.

    .. versionchanged:: 0.3
        Added root_ids
    """
    field_map = dict(
        parent_field='parent',
        uri_field='uri',
        title_field='title',
    )
    if field_map_changes is not None:
        field_map.update(field_map_changes)
    if root_ids is None:
        root_ids = tuple()

    def recurse(node, level=1):
        if level > max_depth or not node:
            return []
        data_pair = (
            url_for(endpoint, uri=getattr(node, field_map['uri_field'])),
            getattr(node, field_map['title_field'])
        )
        if node.id in root_ids:
            return [data_pair]
        else:
            return [data_pair] + recurse(
                getattr(node, field_map['parent_field']), level + 1
            )

    items = recurse(browse_record)

    if add_home:
        items.append((url_for('nereid.website.home'), 'Home'))

    # The bread crumb is now in reverse order with home at end, reverse it
    items.reverse()

    return items
