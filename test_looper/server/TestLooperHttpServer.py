import cherrypy
import dateutil.parser
import itertools
import math
import os
import sys
import yaml
import time
import logging
import tempfile
import threading
import markdown
import urllib
import urlparse
import pytz
import simplejson
import struct
import os
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.source_control as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.server.TestLooperHtmlRendering as TestLooperHtmlRendering
import test_looper.server.TestLooperHtmlRenderingDev as TestLooperHtmlRenderingDev

from test_looper.server.TestLooperServer import TerminalInputMsg
import test_looper.core.algebraic_to_json as algebraic_to_json

from ws4py.server.cherrypyserver import WebSocketPlugin, WebSocketTool
from ws4py.websocket import WebSocket

import traceback

time.tzset()

MAX_BYTES_TO_SEND = 100000

class LogHandler:
    def __init__(self, testManager, testId, websocket):
        self.testManager = testManager
        self.testId = testId
        self.websocket = websocket

        testManager.heartbeatHandler.addListener(testId, self.logMsg)

    def logMsg(self, message):
        if len(message) > MAX_BYTES_TO_SEND:
            message = message[-MAX_BYTES_TO_SEND:]

        try:
            message = message.replace("\n","\n\r")

            self.websocket.send(message, False)
        except:
            logging.error("error in websocket handler:\n%s", traceback.format_exc())
            raise

    def onData(self, message):
        pass

    def onClosed(self):
        pass

class InteractiveEnvironmentHandler:
    def __init__(self, testManager, deploymentId, websocket):
        self.testManager = testManager
        self.deploymentId = deploymentId
        self.websocket = websocket
        self.buffer = ""

        testManager.subscribeToDeployment(self.deploymentId, self.onTestOutput)

    def onTestOutput(self, message):
        try:
            if message is None:
                return
                
            if len(message) > MAX_BYTES_TO_SEND:
                message = message[-MAX_BYTES_TO_SEND:]

            self.websocket.send(message, False)
        except:
            logging.error("Error in websocket handler: \n%s", traceback.format_exc())
            raise

    def onData(self, message):
        try:
            self.buffer += message

            while len(self.buffer) >= 4:
                which_msg = struct.unpack(">i", self.buffer[:4])[0]

                if which_msg == 0:
                    if len(self.buffer) < 8:
                        return

                    #this is a data message
                    bytes_expected = struct.unpack(">i", self.buffer[4:8])[0]

                    if len(self.buffer) >= bytes_expected + 8:
                        msg = TerminalInputMsg.KeyboardInput(bytes=self.buffer[8:8+bytes_expected])
                        self.buffer = self.buffer[8+bytes_expected:]
                        self.testManager.writeMessageToDeployment(self.deploymentId, msg)
                    else:
                        return

                elif which_msg == 1:
                    if len(self.buffer) < 12:
                        return
                    
                    #this is a console resize-message
                    cols = struct.unpack(">i", self.buffer[4:8])[0]
                    rows = struct.unpack(">i", self.buffer[8:12])[0]

                    self.buffer = self.buffer[12:]

                    msg = TerminalInputMsg.Resize(cols=cols, rows=rows)

                    self.testManager.writeMessageToDeployment(self.deploymentId, msg)
                else:
                    self.websocket.close()
                    return
        except:
            logging.error("Error in websocket handler:\n%s", traceback.format_exc())

    def onClosed(self):
        self.testManager.unsubscribeFromDeployment(self.deploymentId, self.onTestOutput)



def MakeWebsocketHandler(httpServer):
    def caller(*args, **kwargs):
        everGotAMessage = [False]
        handler = [None]

        class WebsocketHandler(WebSocket):
            def logMsg(self, message):
                try:
                    self.send(message.replace("\n","\n\r"), False)
                except:
                    logging.error("error in websocket handler:\n%s", traceback.format_exc())

            def initialize(self):
                try:
                    try:
                        query = urlparse.parse_qs(urlparse.urlparse(self.environ["REQUEST_URI"]).query)
                    except:
                        self.send("Invalid query string.", False)
                        return

                    if "testId" in query:
                        handler[0] = LogHandler(httpServer.testManager, query["testId"][0], self)
                    elif "deploymentId" in query:
                        handler[0] = InteractiveEnvironmentHandler(
                            httpServer.testManager,
                            query['deploymentId'][0],
                            self 
                            )
                    else:
                        logging.error("Invalid query string: %s", self.environ["REQUEST_URI"])
                        self.send("Invalid query string.", False)
                        return
                except:
                    logging.error("error in websocket handler:\n%s", traceback.format_exc())

            def received_message(self, message):
                try:
                    msg = message.data

                    if not everGotAMessage[0]:
                        everGotAMessage[0] = True
                        self.initialize()

                    if handler[0]:
                        handler[0].onData(msg)
                except:
                    logging.error("error in websocket handler:\n%s", traceback.format_exc())

            def closed(self, *args):
                try:
                    WebSocket.closed(self, *args)

                    if handler[0]:
                        handler[0].onClosed()
                except:
                    logging.error("error in websocket handler:\n%s", traceback.format_exc())


        return WebsocketHandler(*args, **kwargs)
    return caller

class TestLooperHttpServer(object):
    def __init__(self,
                 portConfig,
                 serverConfig,
                 testManager,
                 machine_management,
                 artifactStorage,
                 src_ctrl,
                 event_log
                 ):
        """Initialize the TestLooperHttpServer

        testManager - a TestManager.TestManager object
        httpPortOverride - the port to listen on for http requests
        """
        self.testManager = testManager
        self.machine_management = machine_management
        self.httpPort = portConfig.server_https_port
        self.src_ctrl = src_ctrl
        self.eventLog = event_log
        self.eventLog.addLogMessage("test-looper", "TestLooper initialized")
        self.defaultCoreCount = 4
        self.artifactStorage = artifactStorage
        self.certs = serverConfig.path_to_certs.val if serverConfig.path_to_certs.matches.Value else None
        self.address = ("https" if self.certs else "http") + "://" + portConfig.server_address + ":" + str(portConfig.server_https_port)
        self.websocket_address = ("wss" if self.certs else "ws") + "://" + portConfig.server_address + ":" + str(portConfig.server_https_port)
        
        self.accessTokenHasPermission = {}

        self.regular_renderer = TestLooperHtmlRendering.Renderer(self)
        self.dev_renderer = TestLooperHtmlRenderingDev.Renderer(self)
        self.dev_filename = TestLooperHtmlRenderingDev.__file__.replace(".pyc",".py")
        self.dev_modtime = os.path.getmtime(self.dev_filename)

    @property
    def renderer(self):
        mtime = os.path.getmtime(self.dev_filename)

        if mtime > self.dev_modtime:
            logging.info("Reimporting the dev html rendering module")
            self.dev_modtime = mtime
            reload(TestLooperHtmlRenderingDev)

            self.dev_renderer = TestLooperHtmlRenderingDev.Renderer(self)

        if cherrypy.session.get("dev_enabled", False):
            return self.dev_renderer
        else:
            return self.regular_renderer

    @cherrypy.expose
    def enableDev(self, redirect = "/repos"):
        cherrypy.session["dev_enabled"] = True
        raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def disableDev(self, redirect = "/repos"):
        cherrypy.session["dev_enabled"] = True
        raise cherrypy.HTTPRedirect(redirect)

    def addLogMessage(self, format_string, *args, **kwargs):
        self.eventLog.addLogMessage(self.getCurrentLogin(), format_string, *args, **kwargs)

    def getCurrentLogin(self):
        login = cherrypy.session.get('github_login', None)
        if login is None and self.is_authenticated():
            token = self.access_token()
            login = cherrypy.session['github_login'] = self.src_ctrl.getUserNameFromToken(token)
        return login or "Guest"


    def authenticate(self):
        auth_url = self.src_ctrl.authenticationUrl()

        if auth_url is not None:
            #stash the current url
            self.save_current_url()
            raise cherrypy.HTTPRedirect(auth_url)
        else:
            cherrypy.session['github_access_token'] = "DUMMY"


    def save_current_url(self):
        cherrypy.session['redirect_after_authentication'] = self.currentUrl()


    @staticmethod
    def is_authenticated():
        return 'github_access_token' in cherrypy.session


    @staticmethod
    def access_token():
        return cherrypy.session['github_access_token']


    def can_write(self):
        if not self.is_authenticated():
            return False

        token = self.access_token()
        is_authorized = self.accessTokenHasPermission.get(token)
        if is_authorized is None:
            is_authorized = self.src_ctrl.authorize_access_token(token)
            self.accessTokenHasPermission[token] = is_authorized

            self.addLogMessage(
                "Authorization: %s",
                "Granted" if is_authorized else "Denied"
                )
        return is_authorized


    def authorize(self, read_only):
        if not self.is_authenticated():
            # this redirects to the login page. Authorization will take place
            # again once the user is redirected back to the app.
            self.authenticate()
        else:
            if self.can_write():
                return

            message = (
                "You are not authorized to access this repository" if read_only else
                "You are not authorized to perform the requested operation"
                )

            raise cherrypy.HTTPError(403, message)


    @cherrypy.expose
    def logout(self):
        token = cherrypy.session.pop('github_access_token', None)
        if token and token in self.accessTokenHasPermission:
            del self.accessTokenHasPermission[token]

        cherrypy.session.pop('github_login')

        raise cherrypy.HTTPRedirect(self.address + "/")


    @cherrypy.expose
    def githubAuthCallback(self, code):
        # kept for backward compatibility
        return self.oauth_callback(code)

    @cherrypy.expose
    def oauth_callback(self, code):
        access_token = self.src_ctrl.getAccessTokenFromAuthCallbackCode(code)
        if not access_token:
            logging.error("Failed to accquire access token")
            raise cherrypy.HTTPError(401, "Unable to authenticate your session")

        logging.info("Access token is %s", access_token)

        cherrypy.session['github_access_token'] = access_token

        raise cherrypy.HTTPRedirect(
            cherrypy.session.pop('redirect_after_authentication', None) or self.address + "/"
            )


    @cherrypy.expose
    def index(self):
        raise cherrypy.HTTPRedirect(self.address + "/repos")

    @cherrypy.expose
    def test(self, testId):
        self.authorize(read_only=True)

        return self.renderer.test(testId)

    @cherrypy.expose
    def test_contents(self, testId, key):
        return self.renderer.test_contents(testId, key)

    @cherrypy.expose
    def clearTestRun(self, testId, redirect):
        self.authorize(read_only=False)

        return self.renderer.clearTestRun(testId, redirect)

    @cherrypy.expose
    def testLogs(self, testId):
        return self.renderer.testLogs(testId)

    @cherrypy.expose
    def build_contents(self, repoName, commitHash, key):
        return self.renderer.build_contents(repoName, commitHash, key)

    @cherrypy.expose
    def cancelTestRun(self, testRunId, redirect):
        self.authorize(read_only=False)
            
        return self.renderer.cancelTestRun(testRunId, redirect)

    @cherrypy.expose
    def machines(self):
        self.authorize(read_only=True)

        return self.renderer.machines()

    @cherrypy.expose
    def commit(self, repoName, commitHash):
        self.authorize(read_only=True)

        return self.renderer.commit(repoName, commitHash)

    @cherrypy.expose
    def allTestRuns(self, repoName, commitHash, failuresOnly=False, testName=None):
        self.authorize(read_only=True)

        return self.renderer.allTestRuns(repoName, commitHash, failuresOnly, testName)

    @cherrypy.expose
    def bootDeployment(self, fullname):
        return self.renderer.bootDeployment(fullname)

    @cherrypy.expose
    def testEnvironment(self, repoName, commitHash, environmentName):
        return self.renderer.testEnvironment(repoName, commitHash, environmentName)

    @cherrypy.expose
    def toggleCommitUnderTest(self, reponame, hash, redirect):
        self.authorize(read_only=False)

        return self.renderer.toggleCommitUnderTest(reponame, hash, redirect)

    @cherrypy.expose
    def toggleBranchUnderTest(self, repo, branchname, redirect):
        self.authorize(read_only=False)

        return self.renderer.toggleBranchUnderTest(repo, branchname, redirect)

    @cherrypy.expose
    def refresh(self, reponame=None, redirect=None):
        if reponame is None:
            self.testManager.markRepoListDirty(time.time())
        else:
            self.testManager.markBranchListDirty(reponame, time.time())

        raise cherrypy.HTTPRedirect(redirect or self.address + "/repos")

    
    @cherrypy.expose
    def deployments(self):
        self.authorize(read_only=True)
    
        return self.renderer.deployments()

    @cherrypy.expose
    def shutdownDeployment(self, deploymentId):
        return self.renderer.shutdownDeployment(deploymentId)

    @cherrypy.expose
    def repos(self, groupings=None):
        self.authorize(read_only=True)

        return self.renderer.repos(groupings=groupings)

    @cherrypy.expose
    def branches(self, repoName, groupings=None):
        self.authorize(read_only=True)

        return self.renderer.branches(repoName, groupings=groupings)

    @cherrypy.expose
    def toggleBranchTestTargeting(self, reponame, branchname, testType, testGroupsToExpand):
        self.authorize(read_only=False)

        return self.renderer.toggleBranchTestTargeting(reponame, branchname, testType, testGroupsToExpand)

    @cherrypy.expose
    def branch(self, reponame, branchname, **kwargs):
        self.authorize(read_only=True)

        return self.renderer.branch(reponame, branchname, **kwargs)

    @cherrypy.expose
    def updateBranchPin(self, repoName, branchName, ref, redirect):
        return self.renderer.updateBranchPin(repoName, branchName, ref, redirect)


    @cherrypy.expose
    def eventLogs(self):
        self.authorize(read_only=True)
        return self.renderer.eventLogs()

    @cherrypy.expose
    def githubReceivedAPush(self):
        return self.webhook()

    @cherrypy.expose
    def webhook(self, *args, **kwds):
        if 'Content-Length' not in cherrypy.request.headers:
            raise cherrypy.HTTPError(400, "Missing Content-Length header")

        if cherrypy.request.headers['Content-Type'] == "application/x-www-form-urlencoded":
            payload = simplejson.loads(cherrypy.request.body_params['payload'])
        else:
            payload = simplejson.loads(cherrypy.request.body.read(int(cherrypy.request.headers['Content-Length'])))

        event = self.src_ctrl.verify_webhook_request(cherrypy.request.headers, payload)

        if not event:
            logging.error("Invalid webhook request")
            raise cherrypy.HTTPError(400, "Invalid webhook request")

        #don't block the webserver itself, so we can do this in a background thread
        logging.info("Triggering refresh branches on repo=%s branch=%s", event['repo'], event['branch'])
        self.testManager.markBranchListDirty(event['repo'], time.time())

    @cherrypy.expose
    def interactive_socket(self, **kwargs):
        pass

    @cherrypy.expose
    def terminalForTest(self, testId):
        return self.websocketText(urllib.urlencode({"testId":testId}))

    @cherrypy.expose
    def terminalForDeployment(self, deploymentId):
        return self.websocketText(urllib.urlencode({"deploymentId":deploymentId}))

    @cherrypy.expose
    def machineHeartbeatMessage(self, machineId, heartbeatmsg):
        self.testManager.machineHeartbeat(machineId, time.time(), heartbeatmsg)

    def websocketText(self, urlQuery):
        return """
        <!doctype html>
        <html lang="en">

        <head>
            <meta charset="UTF-8">
            <title>TestLooper Interactive</title>
            <script src="/js/hterm_all.js"></script>
            <script>
            var term;
            var websocket;
            var address = "__websocket_address__";

            if (window.WebSocket) { 
                websocket = new WebSocket(address, ['protocol']); 
            }
            else if (window.MozWebSocket) {
                websocket = MozWebSocket(address);
            }
            else {
                console.log('WebSocket Not Supported');
            }

            var buf = '';

            function Terminal(argv) {
                this.argv_ = argv;
                this.io = null;
                this.pid_ = -1;
            }

            Terminal.prototype.run = function() {
                this.io = this.argv_.io.push();

                this.io.onVTKeystroke = this.sendString_.bind(this);
                this.io.sendString = this.sendString_.bind(this);
                this.io.onTerminalResize = this.onTerminalResize.bind(this);
            }

            function toBytesInt32 (num) {
                arr = new ArrayBuffer(4); // an Int32 takes 4 bytes
                view = new DataView(arr);
                view.setUint32(0, num, false); // byteOffset = 0; litteEndian = false
                return arr;
            }

            Terminal.prototype.sendString_ = function(str) {
                websocket.send(toBytesInt32(0))
                websocket.send(toBytesInt32(str.length))
                websocket.send(str);
            };

            Terminal.prototype.onTerminalResize = function(col, row) {
                websocket.send(toBytesInt32(1))
                websocket.send(toBytesInt32(col))
                websocket.send(toBytesInt32(row))
            };

            websocket.onopen = function() {
                lib.init(function() {
                    hterm.defaultStorage = new lib.Storage.Local();
                    term = new hterm.Terminal();
                    window.term = term;
                    term.decorate(document.getElementById('terminal'));

                    term.setCursorPosition(0, 0);
                    term.setCursorVisible(true);
                    term.prefs_.set('ctrl-c-copy', true);
                    term.prefs_.set('ctrl-v-paste', true);
                    term.prefs_.set('use-default-window-copy', true);

                    term.runCommandClass(Terminal, document.location.hash.substr(1));
                    
                    Terminal.prototype.onTerminalResize(term.screenSize.width, term.screenSize.height)

                    if (buf && buf != '')
                    {
                        term.io.writeUTF16(buf);
                        buf = '';
                    }
                });
            };

            websocket.onclose = function(event) {
                term.io.writeUTF16("\\r\\n\\r\\n\\r\\n<<<<DISCONNECTED>>>>>>\\r\\n\\r\\n\\r\\n")
            };

            websocket.onmessage = function(data) {
                if (!term) {
                    buf += data.data;
                    return;
                }
                term.io.writeUTF16(data.data);
            };
            
            </script>
            <style>
                html,
                body {
                    height: 100%;
                    width: 100%;
                    margin: 0px;
                }
                #terminal {
                    display: block;
                    position: relative;
                    width: 100%;
                    height: 100%;
                }
            </style>
        </head>

        <body>
            <div id="terminal"></div>
        </body>

        </html>
        """.replace("__websocket_address__", self.websocket_address + "/interactive_socket?" + urlQuery)

    def start(self):
        config = {
            'global': {
                "engine.autoreload.on":False,
                'server.socket_host': '0.0.0.0',
                'server.socket_port': self.httpPort,
                'server.show_tracebacks': False,
                'request.show_tracebacks': False,
                'tools.sessions.on': True,
                }
            }

        if self.certs:
            config['global'].update({
                'server.ssl_module': 'builtin',
                'server.ssl_certificate':self.certs.cert,
                'server.ssl_private_key':self.certs.key,
                'server.ssl_certificate_chain':self.certs.chain
                })

        cherrypy.config.update(config)
        
        cherrypy.tools.websocket = WebSocketTool()

        logging.info("STARTING HTTP SERVER")

        current_dir = os.path.dirname(__file__)
        path_to_source_root = os.path.abspath(os.path.join(current_dir, "..", ".."))

        temp_dir_for_tarball = tempfile.mkdtemp()

        SubprocessRunner.callAndAssertSuccess(
            ["tar", "cvfz", os.path.join(temp_dir_for_tarball, "test_looper.tar.gz"), 
                "--directory", path_to_source_root, "test_looper"
            ])

        with DirectoryScope.DirectoryScope(path_to_source_root):
            SubprocessRunner.callAndAssertSuccess(
                ["zip", "-r", os.path.join(temp_dir_for_tarball, "test_looper.zip"), "test_looper", "-x", "*.pyc", "*.js"]
                )

        with DirectoryScope.DirectoryScope(temp_dir_for_tarball):
            SubprocessRunner.callAndReturnOutput(
                ["curl", "https://bootstrap.pypa.io/get-pip.py", "-O", os.path.join(temp_dir_for_tarball, "get-pip.py")]
                )
            assert os.path.exists(os.path.join(temp_dir_for_tarball, "get-pip.py"))

        cherrypy.tree.mount(self, '/', {
            '/favicon.ico': {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(current_dir,
                                                          'content',
                                                          'favicon.ico')
                },
            '/get-pip.py': {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(temp_dir_for_tarball,
                                                          'get-pip.py')
                },
            '/test_looper.tar.gz': {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(temp_dir_for_tarball,
                                                          'test_looper.tar.gz')
                },
            '/test_looper.zip': {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(temp_dir_for_tarball,
                                                          'test_looper.zip')
                },
            '/css': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': os.path.join(current_dir, 'css')
                },
            '/js': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': os.path.join(current_dir, 'content', 'js')
                },
            '/interactive_socket': {
                'tools.websocket.on': True, 
                'tools.websocket.handler_cls': MakeWebsocketHandler(self),
                'tools.websocket.protocols': ['protocol']
                }
            })

        cherrypy.server.socket_port = self.httpPort

        cherrypy.engine.autoreload.on = False

        cherrypy.engine.signals.subscribe()

        WebSocketPlugin(cherrypy.engine).subscribe()

        cherrypy.engine.start()

    @staticmethod
    def stop():
        logging.info("Stopping cherrypy engine")
        cherrypy.engine.exit()
        cherrypy.server.httpserver = None
        logging.info("Cherrypy engine stopped")
