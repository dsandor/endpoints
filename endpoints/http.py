# -*- coding: utf-8 -*-
from __future__ import unicode_literals, division, print_function, absolute_import
import os
import urllib
import json
import types
import cgi
import re
import base64
from BaseHTTPServer import BaseHTTPRequestHandler
from functools import partial
from wsgiref.headers import Headers as BaseHeaders
from collections import Mapping, MutableSequence, Sequence
import itertools
import logging
import inspect
import copy

try:
    import urlparse
except ImportError:
    from urllib import parse as urlparse

from .decorators import _property
from .utils import AcceptHeader, ByteString, MimeType


logger = logging.getLogger(__name__)


class Headers(BaseHeaders, Mapping):
    """handles headers, see wsgiref.Headers link for method and use information

    Handles normalizing of header names, the problem with headers is they can
    be in many different forms and cases and stuff (eg, CONTENT_TYPE and Content-Type),
    so this handles normalizing the header names so you can request Content-Type
    or CONTENT_TYPE and get the same value.

    This has the same interface as Python's built-in wsgiref.Headers class but
    makes it even more dict-like and will return titled header names when iterated
    or anything (eg, Content-Type instead of all lowercase content-type)

    Here is the headers spec:
        https://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html

    Here are the wsgiref class docs:
        https://docs.python.org/2/library/wsgiref.html#module-wsgiref.headers
        https://hg.python.org/cpython/file/2.7/Lib/wsgiref/headers.py
    """
    @classmethod
    def normalize_name(cls, k):
        """converts things like FOO_BAR to Foo-Bar which is the normal form"""
        klower = k.lower().replace('_', '-')
        bits = klower.split('-')
        return "-".join((bit.title() for bit in bits))

    def __init__(self, headers=None, **kwargs):
        super(Headers, self).__init__([])
        self.update(headers, **kwargs)

    def __setitem__(self, name, val):
        name = self.normalize_name(name)
        return super(Headers, self).__setitem__(name, val)

    def __delitem__(self, name):
        name = self.normalize_name(name)
        return super(Headers, self).__delitem__(name)

    def get_all(self, name):
        name = self.normalize_name(name)
        return super(Headers, self).get_all(name)

    def get(self, name, default=None):
        name = self.normalize_name(name)
        return super(Headers, self).get(name, default)

    def setdefault(self, name, val):
        name = self.normalize_name(name)
        return super(Headers, self).setdefault(name, val)

    def add_header(self, name, val, **params):
        name = self.normalize_name(name)
        return super(Headers, self).add_header(name, val, **params)

    def keys(self):
        return [self.normalize_name(k) for k, v in self._headers]

    def items(self):
        for k, v in self._headers:
            yield self.normalize_name(k), v

    def iteritems(self):
        return self.items()

    def iterkeys(self):
        for k in self.keys():
            yield k

    def __iter__(self):
        for k, v in self._headers:
            yield self.normalize_name(k)

    def pop(self, name, *args, **kwargs):
        val = self.get(name)
        if val is None:
            if args:
                val = args[0]
            elif "default" in kwargs:
                val = kwargs["default"]
            else:
                raise KeyError(name)

        else:
            del self[name]

        return val

    def update(self, headers, **kwargs):
        if not headers: headers = {}
        if isinstance(headers, Mapping):
            headers.update(kwargs)
            headers = headers.items()

        else:
            if kwargs:
                headers = itertools.chain(
                    headers,
                    kwargs.items()
                )

        for k, v in headers:
            self[k] = v

    def copy(self):
        return self.__deepcopy__()

    def __deepcopy__(self):
        return type(self)(self._headers)


class RequestBody(object):
    """this is the normalized request environment that every interface needs to
    conform to, it primarily acts like a wsgi environment, which is compatible with
    python's internal cgi.FieldStorage stuff"""

    # https://hg.python.org/cpython/file/2.7/Lib/cgi.py#l325

    def __init__(self, fp, headers, environ):
        self.headers = headers
        self.environ = environ
        self.fp = fp

        # make sure environ has the bare minimum to work
        for k in ["REQUEST_METHOD", "QUERY_STRING"]:
            if k not in self.environ:
                raise ValueError("environ dict does not contain {}".format(k))

    def __iter__(self):
        body_fields = cgi.FieldStorage(
            fp=self.fp,
            headers=self.headers,
            environ=self.environ,
            keep_blank_values=True
        )

        for field_name in body_fields.keys():
            body_field = body_fields[field_name]
            if body_field.filename:
                yield field_name, body_field

            else:
                yield field_name, body_field.value


class ResponseBody(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, types.GeneratorType):
            return [x for x in obj]

        elif isinstance(obj, Exception):
            return {
                "errmsg": str(obj)
            }

        else:
            return json.JSONEncoder.default(self, obj)


class Url(str):
    """a url object on steroids, this is here to make it easy to manipulate urls
    we try to map the supported fields to their urlparse equivalents, with some additions

    https://tools.ietf.org/html/rfc3986.html

    given a url http://user:pass@foo.com:1000/bar/che?baz=boom#anchor
    with a controller: Bar

    .scheme = http
    .netloc = user:pass@foo.com:1000
    .hostloc = foo.com:1000
    .hostname = foo.com
    .host = http://foo.com
    .port = 1000
    .base = http://user:pass@foo.com:1000/bar/che
    .fragment = anchor
    .anchor = fragment
    .uri = /bar/che?baz=boom#anchor
    .host(...) = httop://foo.com/...
    .base(...) = httop://foo.com/bar/che/...
    .controller(...) = httop://foo.com/bar/...
    """
    encoding = "utf-8"

    scheme = "http"

    username = None

    password = None

    hostname = ""

    port = None

    netloc = ""

    path = ""

    query_kwargs = {}

    fragment = ""

    controller_path = ""

    @property
    def root(self):
        """just return scheme://netloc"""
        return urlparse.urlunsplit((
            self.scheme,
            self.netloc,
            "",
            "",
            ""
        ))

    @property
    def anchor(self):
        """alternative name for fragment"""
        return self.fragment

    @property
    def uri(self):
        """return the uri, which is everything but base (no scheme, host, etc)"""
        uristring = self.path
        if self.query:
            uristring += "?{}".format(self.query)
        if self.fragment:
            uristring += "#{}".format(self.fragment)

        return uristring

    def __new__(cls, urlstring=None, **kwargs):
        parts = cls.merge(urlstring, **kwargs)
        urlstring = parts.pop("urlstring")
        instance = super(Url, cls).__new__(cls, urlstring)
        for k, v in parts.items():
            setattr(instance, k, v)
        return instance

    @classmethod
    def keys(cls):
        keys = set(k for k, v in inspect.getmembers(cls) if not k.startswith("__") and not callable(v))
        for dk in ["encoding", "root", "anchor", "uri"]:
            keys.discard(dk)
        return keys

    @classmethod
    def merge(cls, urlstring="", **kwargs):
        # we handle port before any other because the port of host:port in hostname takes precedence
        # the port on the host would take precedence because proxies mean that the
        # host can be something:10000 and the port could be 9000 because 10000 is
        # being proxied to 9000 on the machine, but we want to automatically account
        # for things like that and then if custom behavior is needed then this method
        # can be overridden
        parts = {
            "hostname": cls.hostname,
            "port": cls.port,
            "query_kwargs": dict(cls.query_kwargs),
            "controller_path": cls.controller_path,
            "scheme": cls.scheme,
            "netloc": cls.netloc,
            "path": cls.path,
            "fragment": cls.fragment,
            "username": cls.username,
            "password": cls.password,
        }

        if urlstring:
            properties = [
                "scheme",
                "netloc",
                "path",
                "fragment",
                "username",
                "password",
                "hostname",
                "port",
                "query",
            ]

            o = urlparse.urlsplit(str(urlstring))
            if o.scheme and o.netloc: # full url 
                for k in properties:
                    v = getattr(o, k)
                    parts[k] = v

            elif o.scheme and o.path: # no scheme: host/some/path
                # we need to better normalize to account for port
                hostname, path = urlstring.split("/", 1)
                parts["hostname"] = hostname
                if "?" in path:
                    path, query = path.split("?", 1)
                    parts["path"] = path
                    parts["query"] = query

                else:
                    parts["path"] = path

            else:
                parts["hostname"] = o.path

            query = parts.get("query", "")
            if query:
                parts["query_kwargs"].update(cls.parse_query(query))

        query = kwargs.pop("query", "")
        if query:
            parts["query_kwargs"].update(cls.parse_query(query))

        query_kwargs = kwargs.pop("query_kwargs", {})
        if query_kwargs:
            parts["query_kwargs"].update(query_kwargs)

        parts["query"] = ""
        if parts["query_kwargs"]:
            parts["query"] = cls.unparse_query(parts["query_kwargs"])

        for k, v in kwargs.items():
            parts[k] = v

        common_ports = set([80, 443])
        domain, port = cls.split_hostname_from_port(parts["hostname"])
        parts["hostname"] = domain
        if port:
            parts["port"] = kwargs.get("port", port)

        if not parts.get("port", None):
            if parts["scheme"] == "http":
                parts["port"] = 80
            elif parts["scheme"] == "https":
                parts["port"] = 443

        if not parts.get("hostloc", ""):
            hostloc = parts["hostname"]
            port = parts["port"]
            if port and port not in common_ports:
                hostloc = '{}:{}'.format(hostloc, port)
            parts["hostloc"] = hostloc

        if not parts.get("netloc", ""):
            parts["netloc"] = parts["hostloc"]

        username = kwargs.get("username", None)
        password = kwargs.get("password", None)
        merge_netloc = username or password

        if merge_netloc:
            if not username: username = parts["username"]
            if not password: password = parts["password"]
            if username:
                parts["netloc"] = "{}:{}@{}".format(
                    kwargs.get("username", parts["username"]),
                    password if password else "",
                    parts["hostloc"]
                )

        # we don't want common ports to be a part of a .geturl() call, but we do
        # want .port to return them
        if not merge_netloc:
            for common_port in common_ports:
                port_str = ":{}".format(common_port)
                if parts["netloc"].endswith(port_str):
                    parts["netloc"] = parts["netloc"][:-len(port_str)]

        parts["path"] = "/".join(cls.normalize_paths(parts["path"]))

        parts["urlstring"] = urlparse.urlunsplit((
            parts["scheme"],
            parts["netloc"],
            parts["path"],
            parts["query"],
            parts["fragment"],
        ))

        for k in parts:
            if isinstance(parts[k], bytes):
                parts[k] = parts[k].decode(cls.encoding)

        if parts["port"]:
            parts["port"] = int(parts["port"])

        return parts

    @classmethod
    def parse_query(cls, query):
        """return name=val&name2=val2 strings into {name: val} dict"""
        if not query: return {}

        d = {}
        # https://docs.python.org/2/library/urlparse.html
        for k, kv in urlparse.parse_qs(query, True, strict_parsing=True).items():
            #k = k.rstrip("[]") # strip out php type array designated variables
            if len(kv) > 1:
                d[k] = kv
            else:
                d[k] = kv[0]

        return d

    @classmethod
    def unparse_query(cls, query_kwargs):
        return urllib.urlencode(query_kwargs, doseq=True)

    @classmethod
    def normalize_paths(cls, *paths):
        args = []
        for ps in paths:
            if isinstance(ps, basestring):
                args.extend(filter(None, ps.split("/")))
                #args.append(ps.strip("/"))
            else:
                for p in ps:
                    args.extend(cls.normalize_paths(p))
        return args

    def _normalize_params(self, *paths, **query_kwargs):
        """a lot of the helper methods are very similar, this handles their arguments"""
        kwargs = {}

        if paths:
            fragment = paths[-1]
            if fragment:
                if fragment.startswith("#"):
                    kwargs["fragment"] = fragment
                    paths.pop(-1)

            kwargs["path"] = "/".join(self.normalize_paths(*paths))

        kwargs["query_kwargs"] = query_kwargs
        return kwargs

    @classmethod
    def split_hostname_from_port(cls, hostname):
        """given a hostname:port return a tuple (hostname, port)"""
        bits = hostname.split(":", 2)
        p = None
        d = bits[0]
        if len(bits) == 2:
            p = int(bits[1])

        return d, p

    def create(self, *args, **kwargs):
        return type(self)(*args, **kwargs)

    def add(self, **kwargs):
        """Just a shortcut to change the current url, equivalent to Url(self, **kwargs)"""
        if "path" in kwargs:
            if not kwargs["path"][0].startswith("/"):
                paths = self.normalize_paths(self.path, kwargs["path"])
            else:
                paths = self.normalize_paths(kwargs["path"])
            kwargs["path"] = "/".join(paths)
        return self.create(self, **kwargs)

    def subtract(self, *paths, **kwargs):
        sub_kwargs = self.jsonable()

        path2 = self.normalize_paths(paths)
        path2.extend(self.normalize_paths(kwargs.pop("path", "")))
        if path2:
            sub_path = self.normalize_paths(self.path)
            for p in path2:
                try:
                    sub_path.remove(p)
                except ValueError:
                    pass

            sub_kwargs["path"] = sub_path

        for k, v in kwargs.items():
            if k == "query_kwargs":
                for qk, qv in kwargs[k].items():
                    if str(sub_kwargs[k][qk]) == str(qv):
                        sub_kwargs[k].pop(qk)

            else:
                if str(sub_kwargs[k]) == str(v):
                    sub_kwargs.pop(k)

        return self.create(**sub_kwargs)

    def controller(self, *paths, **query_kwargs):
        """create a new url object using the controller path as a base

        if you have a controller `foo.BarController` then this would create a new
        Url instance with `host/foo/bar` as the base path, so any *paths will be
        appended to `/foo/bar`

        :example:
            # controller foo.BarController

            print url # http://host.com/foo/bar/some_random_path

            print url.controller() # http://host.com/foo/bar
            print url.controller("che", boom="bam") # http://host/foo/bar/che?boom=bam

        :param *paths: list, the paths to append to the controller path
        :param **query_kwargs: dict, any query string params to add
        """
        kwargs = self._normalize_params(*paths, **query_kwargs)
        if self.controller_path:
            if "path" in kwargs:
                paths = self.normalize_paths(self.controller_path, kwargs["path"])
                kwargs["path"] = "/".join(paths)
            else:
                kwargs["path"] = self.controller_path
        return self.create(self.root, **kwargs)

    def base(self, *paths, **query_kwargs):
        """create a new url object using the current base path as a base

        if you had requested /foo/bar, then this would append *paths and **query_kwargs
        to /foo/bar

        :example:
            # current path: /foo/bar

            print url # http://host.com/foo/bar

            print url.base() # http://host.com/foo/bar
            print url.base("che", boom="bam") # http://host/foo/bar/che?boom=bam

        :param *paths: list, the paths to append to the current path without query params
        :param **query_kwargs: dict, any query string params to add
        """
        kwargs = self._normalize_params(*paths, **query_kwargs)
        if self.path:
            if "path" in kwargs:
                paths = self.normalize_paths(self.path, kwargs["path"])
                kwargs["path"] = "/".join(paths)
            else:
                kwargs["path"] = self.path
        return self.create(self.root, **kwargs)

    def host(self, *paths, **query_kwargs):
        """create a new url object using the host as a base

        if you had requested http://host/foo/bar, then this would append *paths and **query_kwargs
        to http://host

        :example:
            # current url: http://host/foo/bar

            print url # http://host.com/foo/bar

            print url.host_url() # http://host.com/
            print url.host_url("che", boom="bam") # http://host/che?boom=bam

        :param *paths: list, the paths to append to the current path without query params
        :param **query_kwargs: dict, any query string params to add
        """
        kwargs = self._normalize_params(*paths, **query_kwargs)
        return self.create(self.root, **kwargs)

    def copy(self):
        return self.__deepcopy__()

    def __copy__(self):
        return self.__deepcopy__()

    def __deepcopy__(self, memodict={}):
        return self.create(
            scheme=self.scheme,
            username=self.username,
            password=self.password,
            hostname=self.hostname,
            port=self.port,
            path=self.path,
            query_kwargs=self.query_kwargs,
            fragment=self.fragment,
            controller_path=self.controller_path,
        )

    def __add__(self, other):
        ret = ""
        if isinstance(other, Mapping):
            ret = self.add(query_kwargs=other)

        elif isinstance(other, MutableSequence):
            ret = self.add(path=other)

        elif isinstance(other, basestring):
            ret = self.add(path=other)

        elif isinstance(other, Sequence):
            ret = self.add(path=other)

        else:
            raise ValueError("Not sure how to add {}".format(type(other)))

        return ret
    __iadd__ = __add__

    def __truediv__(self, other):
        ret = ""
        if isinstance(other, MutableSequence):
            ret = self.add(path=other)

        elif isinstance(other, basestring):
            ret = self.add(path=other)

        elif isinstance(other, Sequence):
            ret = self.add(path=other)

        else:
            raise ValueError("Not sure how to add {}".format(type(other)))

        return ret
    __itruediv__ = __truediv__

    def __sub__(self, other):
        """Return a new url with other removed"""
        ret = ""
        if isinstance(other, Mapping):
            ret = self.subtract(query_kwargs=other)

        elif isinstance(other, MutableSequence):
            ret = self.subtract(path=other)

        elif isinstance(other, basestring):
            ret = self.subtract(path=other)

        elif isinstance(other, Sequence):
            ret = self.subtract(path=other)

        else:
            raise ValueError("Not sure how to add {}".format(type(other)))

        return ret
    __isub__ = __sub__

    def jsonable(self):
        ret = {}
        for k in self.keys():
            v = getattr(self, k)
            if k == "query_kwargs":
                ret[k] = dict(v)
            else:
                ret[k] = v

        return ret


class Http(object):
    def __init__(self):
        self.headers = Headers()

    def has_header(self, header_name):
        """return true if the header is set"""
        return header_name in self.headers

    def set_headers(self, headers):
        """replace all headers with passed in headers"""
        self.headers = Headers(headers)

    def add_headers(self, headers, **kwargs):
        self.headers.update(headers, **kwargs)

    def set_header(self, header_name, val):
        self.headers[header_name] = val

    def add_header(self, header_name, val, **params):
        self.headers.add_header(header_name, val, **params)

    def get_header(self, header_name, default_val=None):
        """try as hard as possible to get a a response header of header_name,
        rreturn default_val if it can't be found"""
        return self.headers.get(header_name, default_val)

    def _parse_query_str(self, query):
        """return name=val&name2=val2 strings into {name: val} dict"""
        u = Url(query=query)
        return u.query_kwargs

    def _build_body_str(self, b):
        # we are returning the body, let's try and be smart about it and match content type
        ct = self.get_header('content-type')
        if ct:
            ct = ct.lower()
            if ct.rfind("json") >= 0:
                if b:
                    b = json.dumps(b)
                else:
                    b = None

            elif ct.rfind("x-www-form-urlencoded") >= 0:
                b = urllib.urlencode(b, doseq=True)

        return b

    def copy(self):
        """nice handy wrapper around the deepcopy"""
        return copy.deepcopy(self)

    def __deepcopy__(self, memodict={}):
        instance = type(self)()
        for key, val in self.__dict__.items():
            #pout.v(key, val)
            if not key.startswith("_"):
                if val is None:
                    setattr(instance, key, val)

                else:
                    if key == "environ":
                        d = type(val)()
                        for k, v in val.items():
                            if k.lower() == "wsgi.input":
                                d[k] = v
                            else:
                                d[k] = copy.deepcopy(v, memodict)

                        setattr(instance, key, d)

                    elif key == "body_input":
                        setattr(instance, key, val)

                    else:
                        #setattr(instance, key, copy.deepcopy(val, memodict))
                        try:
                            setattr(instance, key, copy.deepcopy(val, memodict))
                        except (AttributeError, TypeError):
                        #except AttributeError:
                            setattr(instance, key, copy.copy(val))

        return instance


class Request(Http):
    '''
    common interface that endpoints uses to decide what to do with the incoming request

    an instance of this class is used by the endpoints Call instance to decide where endpoints
    should route requests, so, many times, you'll need to write a glue function that takes however
    your request data is passed to Python and convert it into a Request instance that endpoints can
    understand

    properties --

    headers -- a dict of all the request headers in { header_name: header_val } format
    path -- the /path/part/of/the/url
    path_args -- tied to path, it's path, but divided by / so all the path bits are returned as a list
    query -- the ?name=val portion of a url
    query_kwargs -- tied to query, the values in query but converted to a dict {name: val}
    '''

    environ = None
    """holds all the values that aren't considered headers but usually get passed with the request"""

    raw_request = None
    """the original raw request that was filtered through one of the interfaces"""

    method = None
    """the http method (GET, POST)"""

    body_input = None
    """the request body input, if this is a POST request"""

    controller_info = None
    """will hold the controller information for the request, populated from the Call"""

    @property
    def accept_encoding(self):
        """The encoding the client requested the response to use"""
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Charset
        ret = ""
        accept_encoding = self.get_header("Accept-Charset", "")
        if accept_encoding:
            bits = re.split(r"\s+", accept_encoding)
            bits = bits[0].split(";")
            ret = bits[0]
        return ret

    @_property
    def encoding(self):
        """the character encoding of the request, usually only set in POST type requests"""
        encoding = None
        ct = self.get_header('content-type')
        if ct:
            ah = AcceptHeader(ct)
            if ah.media_types:
                encoding = ah.media_types[0][2].get("charset", None)

        return encoding

    @property
    def access_token(self):
        """return an Oauth 2.0 Bearer access token if it can be found"""
        access_token = self.get_auth_bearer()
        if not access_token:
            access_token = self.query_kwargs.get('access_token', '')
            if not access_token:
                access_token = self.body_kwargs.get('access_token', '')

        return access_token

    @property
    def client_tokens(self):
        """try and get Oauth 2.0 client id and secret first from basic auth header,
        then from GET or POST parameters

        return -- tuple -- client_id, client_secret
        """
        client_id, client_secret = self.get_auth_basic()
        if not client_id and not client_secret:
            client_id = self.query_kwargs.get('client_id', '')
            client_secret = self.query_kwargs.get('client_secret', '')
            if not client_id and not client_secret:
                client_id = self.body_kwargs.get('client_id', '')
                client_secret = self.body_kwargs.get('client_secret', '')

        return client_id, client_secret

    @_property(read_only=True)
    def ips(self):
        """return all the possible ips of this request, this will include public and private ips"""
        r = []
        names = ['X_FORWARDED_FOR', 'CLIENT_IP', 'X_REAL_IP', 'X_FORWARDED', 
               'X_CLUSTER_CLIENT_IP', 'FORWARDED_FOR', 'FORWARDED', 'VIA',
               'REMOTE_ADDR']

        for name in names:
            vs = self.get_header(name, '')
            if vs:
                r.extend(map(lambda v: v.strip(), vs.split(',')))

            vs = self.environ.get(name, '')
            if vs:
                r.extend(map(lambda v: v.strip(), vs.split(',')))

        return r

    @_property(read_only=True)
    def ip(self):
        """return the public ip address"""
        r = ''

        # this was compiled from here:
        # https://github.com/un33k/django-ipware
        # http://www.ietf.org/rfc/rfc3330.txt (IPv4)
        # http://www.ietf.org/rfc/rfc5156.txt (IPv6)
        # https://en.wikipedia.org/wiki/Reserved_IP_addresses
        format_regex = re.compile(r'\s')
        ip_regex = re.compile(r'^(?:{})'.format(r'|'.join([
            r'0\.', # reserved for 'self-identification'
            r'10\.', # class A
            r'169\.254', # link local block
            r'172\.(?:1[6-9]|2[0-9]|3[0-1])\.', # class B
            r'192\.0\.2\.', # documentation/examples
            r'192\.168', # class C
            r'255\.{3}', # broadcast address
            r'2001\:db8', # documentation/examples
            r'fc00\:', # private
            r'fe80\:', # link local unicast
            r'ff00\:', # multicast
            r'127\.', # localhost
            r'\:\:1' # localhost
        ])))

        ips = self.ips
        for ip in ips:
            if not format_regex.search(ip) and not ip_regex.match(ip):
                r = ip
                break

        return r

    @_property
    def host(self):
        """return the request host"""
        return self.get_header("host")

    @_property
    def scheme(self):
        """return the request scheme (eg, http, https)"""
        scheme = self.environ.get('wsgi.url_scheme', "http")
        return scheme

    @_property
    def port(self):
        """return the server port"""
        return int(self.environ.get('SERVER_PORT', 0))

    @property
    def host_url(self):
        """return the request host as a Url instance"""
        return self.url.host_url()

    @property
    def url(self):
        """return the full request url as an Url() instance"""
        scheme = self.scheme
        host = self.host
        path = self.path
        query = self.query
        port = self.port

        # normalize the port
        host_domain, host_port = Url.split_hostname_from_port(host)
        if host_port:
            port = host_port

        controller_path = ""
        if self.controller_info:
            controller_path = self.controller_info.get("path", "")

        u = Url(
            scheme=scheme,
            hostname=host,
            path=path,
            query=query,
            port=port,
            controller_path=controller_path,
        )
        return u

    @_property
    def path(self):
        """path part of a url (eg, http://host.com/path?query=string)"""
        self._path = ''
        path_args = self.path_args
        path = "/{}".format("/".join(path_args))
        return path

    @_property
    def path_args(self):
        """the path converted to list (eg /foo/bar becomes [foo, bar])"""
        self._path_args = []
        path = self.path
        path_args = filter(None, path.split('/'))
        return path_args

    @_property
    def query(self):
        """query_string part of a url (eg, http://host.com/path?query=string)"""
        self._query = query = ""

        query_kwargs = self.query_kwargs
        if query_kwargs: query = urllib.urlencode(query_kwargs, doseq=True)
        return query

    @_property
    def query_kwargs(self):
        """{foo: bar, baz: che}"""
        self._query_kwargs = query_kwargs = {}
        query = self.query
        if query: query_kwargs = self._parse_query_str(query)
        return query_kwargs

    @_property
    def body(self):
        """return the raw version of the body"""
        body = None
        if self.body_input:
            body = self.body_input.read(self.get_header('content-length', -1))

        return body

    @body.setter
    def body(self, body):
        if hasattr(self, "_body_kwargs"):
            del(self._body_kwargs)

        self.body_input = None
        self._body = body

    @_property
    def body_kwargs(self):
        """
        the request body, if this is a POST request

        this tries to do the right thing with the body, so if you have set the body and
        the content type is json, then it will return the body json decoded, if you need
        the original string body, use body

        example --

            self.body = '{"foo":{"name":"bar"}}'
            b = self.body_kwargs # dict with: {"foo": { "name": "bar"}}
            print self.body # string with: '{"foo":{"name":"bar"}}'
        """
        body_kwargs = {}
        ct = self.get_header("content-type")
        if ct:
            ct = ct.lower()
            if ct.rfind("json") >= 0:
                body = self.body
                if body:
                    body_kwargs = json.loads(body)

            else:
                if self.body_input:
                    body = RequestBody(
                        fp=self.body_input,
                        headers=self.headers,
                        environ=self.environ
                        #environ=self.raw_request
                    )

                    body_kwargs = dict(body)

                else:
                    body = self.body
                    if body:
                        body_kwargs = self._parse_query_str(body)

        return body_kwargs

    @body_kwargs.setter
    def body_kwargs(self, body_kwargs):
        self.body_input = None
        self._body_kwargs = body_kwargs
        self._body = self._build_body_str(body_kwargs)

    @property
    def kwargs(self):
        """combine GET and POST params to be passed to the controller"""
        kwargs = dict(self.query_kwargs)
        kwargs.update(self.body_kwargs)

        return kwargs

    def __init__(self):
        self.environ = Headers()
        super(Request, self).__init__()

    def version(self, content_type="*/*"):
        """
        versioning is based off of this post 
        http://urthen.github.io/2013/05/09/ways-to-version-your-api/
        """
        v = ""
        accept_header = self.get_header('accept', "")
        if accept_header:
            a = AcceptHeader(accept_header)
            for mt in a.filter(content_type):
                v = mt[2].get("version", "")
                if v: break

        return v

    def is_method(self, method):
        """return True if the request method matches the passed in method"""
        return self.method.upper() == method.upper()

    def has_body(self):
        #return self.method.upper() in set(['POST', 'PUT'])
        return True if self.body_kwargs else False
        #return self.method.upper() not in set(['GET'])

    def get_auth_bearer(self):
        """return the bearer token in the authorization header if it exists"""
        access_token = ''
        auth_header = self.get_header('authorization')
        if auth_header:
            m = re.search(r"^Bearer\s+(\S+)$", auth_header, re.I)
            if m: access_token = m.group(1)

        return access_token

    def get_auth_basic(self):
        """return the username and password of a basic auth header if it exists"""
        username = ''
        password = ''
        auth_header = self.get_header('authorization')
        if auth_header:
            m = re.search(r"^Basic\s+(\S+)$", auth_header, re.I)
            if m:
                auth_str = base64.b64decode(m.group(1))
                username, password = auth_str.split(':', 1)

        return username, password


class Response(Http):
    """The Response object, every request instance that comes in will get a
    corresponding Response instance that answers the Request.

    an instance of this class is used to create the text response that will be sent 
    back to the client

    Request has a ._body and .body, the ._body property is the raw value that is
    returned from the Controller method that handled the request, the .body property
    is a string that is ready to be sent back to the client, so it is _body converted
    to a string. The reason _body isn't name body_kwargs is because _body can be
    almost anything (not just a dict)
    """

    encoding = ""

    @property
    def code(self):
        """the http status code to return to the client, by default, 200 if a body is present otherwise 204"""
        code = getattr(self, '_code', None)
        if not code:
            if self.has_body():
                code = 200
            else:
                code = 204

        return code

    @code.setter
    def code(self, v):
        self._code = v

    @property
    def status(self):
        if not getattr(self, '_status', None):
            c = self.code
            status_tuple = BaseHTTPRequestHandler.responses.get(self.code)
            msg = "UNKNOWN"
            if status_tuple: msg = status_tuple[0]
            self._status = msg

        return self._status

    @status.setter
    def status(self, v):
        self._status = v

    @property
    def body(self):
        """return the body, formatted to the appropriate content type"""
        b = getattr(self, "_body", None)
#         b = None
#         if hasattr(self, '_body'):
#             b = self._body
        return self.normalize_body(b)

    @body.setter
    def body(self, v):
        self._body = v
        if self.has_streaming_body():
            filepath = getattr(v, "name", "")
            if filepath:
                mt = MimeType.find_type(filepath)
                filesize = os.path.getsize(filepath)
                self.set_header("Content-Type", mt)
                self.set_header("Content-Length", filesize)
                logger.debug(
                    "Response body set to file: \"{}\" with mimetype: \"{}\" and size: {}".format(
                        filepath,
                        mt,
                        filesize
                    )
                )

            else:
                logger.warn("Response body is a filestream that has no .filepath property")

    def has_body(self):
        return getattr(self, "_body", None) is not None
#         ret = False
#         if hasattr(self, '_body'):
#             r = getattr(self, '_body', None)
#             if r is not None: ret = True
# 
#         return ret

    def has_streaming_body(self):
        """return True if the response body is a file pointer"""
        # http://stackoverflow.com/questions/1661262/check-if-object-is-file-like-in-python
        return hasattr(self._body, "read") if self.has_body() else False

    def normalize_body(self, b):
        """return the body as a string, formatted to the appropriate content type"""
        if b is None: return ByteString(b'', self.encoding)

        if self.is_json():
            # I don't like this, if we have a content type but it isn't one
            # of the supported ones we were returning the exception, which threw
            # Jarid off, but now it just returns a string, which is not best either
            # my thought is we could have a body_type_subtype method that would 
            # make it possible to easily handle custom types
            # eg, "application/json" would become: self.body_application_json(b, is_error)
            b = json.dumps(b, cls=ResponseBody)

        else:
            # just return a string representation of body if no content type
            b = ByteString(b, self.encoding)

        return b

    def is_json(self):
        ct = self.get_header('Content-Type')
        return ct.lower().rfind("json") >= 0 if ct else False

    def __iter__(self):
        if self.has_streaming_body():
            fp = self._body
            if fp.closed:
                raise IOError("cannot read streaming body because pointer is closed")

            # http://stackoverflow.com/questions/15599639/whats-perfect-counterpart-in-python-for-while-not-eof
            for chunk in iter(partial(fp.read, 8192), ''):
                yield ByteString(chunk)

            # close the pointer since we've consumed it
            fp.close()

        else:
            yield self.body

    def set_cors_headers(self, request_headers, custom_response_headers=None):

        allow_headers = request_headers['Access-Control-Request-Headers']
        allow_method = request_headers['Access-Control-Request-Method']
        origin = request_headers['origin']

        cors_headers = {
            'Access-Control-Allow-Origin': origin,
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': allow_method,
            'Access-Control-Allow-Headers': allow_headers,
            'Access-Control-Max-Age': 3600
        }

        if custom_response_headers:
            cors_headers.update(custom_response_headers)

        self.add_headers(cors_headers)

    def is_success(self):
        """return True if this response is considered a "successful" response"""
        code = self.code
        return code < 400


