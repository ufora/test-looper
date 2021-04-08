import unittest
import os
import logging
import tempfile
import urllib

import test_looper_tests.common as common
import test_looper_tests.TestYamlFiles as TestYamlFiles
import test_looper_tests.TestManagerTestHarness as TestManagerTestHarness
import test_looper.server.TestLooperHtmlRendering as TestLooperHtmlRendering
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.data_model.ImportExport as ImportExport
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.Config as Config

common.configureLogging()

class MockHttpServer:
    def __init__(self, testManager):
        self.testManager = testManager
        self.testdir = tempfile.mkdtemp()
        self.httpServerConfig = Config.HttpServerConfig(
            repo_prefixes_to_suppress=[],
            repo_prefixes_to_shorten=[]
            )

        self.artifactStorage = ArtifactStorage.LocalArtifactStorage(
            Config.ArtifactsConfig.LocalDisk(
                path_to_build_artifacts = os.path.join(self.testdir, "build_artifacts"),
                path_to_test_artifacts = os.path.join(self.testdir, "test_artifacts")
                )
            )
        
        self.address = "localhost"
        self.src_ctrl = self.testManager.source_control

    def can_write(self):
        return True

    def is_authenticated(self):
        return True

    def getCurrentLogin(self):
        return "user"

class HtmlRenderingTest(unittest.TestCase):
    def setUp(self):
        self.harness = TestManagerTestHarness.getHarness()
        self.database = self.harness.manager.database
        self.testManager = self.harness.manager
        self.httpServer = MockHttpServer(self.harness.manager)
        self.renderer = TestLooperHtmlRendering.Renderer(self.httpServer)

        self.harness.add_content()
        self.harness.markRepoListDirty()
        self.harness.consumeBackgroundTasks()
        self.harness.enableBranchTesting("repo1", "master")
        self.harness.enableBranchTesting("repo2", "master")


    def getSomeContexts(self):
        return [self.renderer.contextFor(x, {}) for x in self.getSomeObjects()]

    def getSomeObjects(self):
        objects = ["repos", "machines", "deployments"]

        for r in self.database.Repo.lookupAll(isActive=True):
            objects.append(r)
            for b in self.database.Branch.lookupAll(repo=r):
                objects.append(b)
                
                objects.append(ComboContexts.BranchAndFilter(b, "linux", ""))
                objects.append(ComboContexts.BranchAndFilter(b, "", "test_with_individual_failures_1"))

                for c in self.testManager.commitsToDisplayForBranch(b,100):
                    objects.append(c)

                    objects.append(ComboContexts.CommitAndFilter(c,"linux", "test_with_individual_failures_1"))
                    objects.append(ComboContexts.CommitAndFilter(c,"", "test_with_individual_failures_1"))

                    for t in self.testManager.allTestsForCommit(c):
                        objects.append(t)

                        for r in self.database.TestRun.lookupAll(test=t):
                            objects.append(r)

        return objects

    def testContexts(self):
        #validate that the "Context" objects can encode/decode their states in 
        #urls correctly

        def objectAndChildren(o):
            yield o
            context = self.renderer.contextFor(o, {})
            parentContext = context.parentContext()

            for child in parentContext.childContexts(context):
                yield child.primaryObject()

        with self.database.view():
            for o in self.getSomeObjects():
                for object in objectAndChildren(o):
                    objContext = self.renderer.contextFor(object, {})

                    parsed = urllib.parse.urlparse(objContext.urlString())
                    path = [x for x in parsed.path.split("/") if x]

                    kwargs = urllib.parse.parse_qs(parsed.query)

                    parsedContext = self.renderer.getFromEncoding(path, kwargs)

                    self.assertTrue(parsedContext, (path,kwargs))

                    self.assertEqual(parsedContext, objContext)

                    parsedContext.parentContext().childContexts(parsedContext)

    def testRendering(self):
        #render all reachable objects in a few different scenarios

        with self.database.view():
            for c in self.getSomeContexts():
                c.renderWholePage()

        self.harness.consumeBackgroundTasks()
        self.harness.startAllNewTests()

        with self.database.view():
            for c in self.getSomeContexts():
                for view in c.contextViews():
                    c.withOptions(view=view).renderWholePage()

        self.harness.doTestsInPhases()

        with self.database.view():
            for c in self.getSomeContexts():
                for view in c.contextViews():
                    c.renderWholePage()
