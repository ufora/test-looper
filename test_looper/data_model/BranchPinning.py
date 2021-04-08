import test_looper.core.GraphUtil as GraphUtil
import test_looper.core.algebraic_to_json as algebraic_to_json
import logging
import re


def unpackCommitPinUpdateMessage(msg):
    """if 'msg' is a commit-pin update message, return the repo, branch, and sha-hash of the updated pin."""
    lines = msg.split("\n")

    if len(lines) < 4:
        return None

    firstline = "Updating pin "
    secondline = "New commit in pinned branch "
    thirdline = "    commit "

    if not lines[0].startswith(firstline):
        return None

    if lines[1].strip():
        return None

    if not lines[2].startswith(secondline):
        return None

    if not lines[3].startswith(thirdline):
        return None

    hash = lines[3][len(thirdline) :].strip()
    ref_name = lines[0][len(firstline) :].split(":")[0]

    # there's a : at the end of the message
    repoAndBranch = lines[2][len(secondline) :].strip()[:-1]
    repo = "/".join(repoAndBranch.split("/")[:-1])
    branch = repoAndBranch.split("/")[-1]

    return repo, branch, hash, ref_name


class BranchPinning:
    def __init__(self, database, source_control):
        self.database = database
        self.source_control = source_control
        self.branches_updated = {}

    def pinGetPinnedToBranchAndCommit(self, pin):
        """Given a BranchPin object, return the (Branch,Commit) pair that it's pinned to."""

        repo_def = pin.repo_def

        curRef = pin.branch.head.data.repos[repo_def]

        # this is what we're currently referencing
        repoName = curRef.reponame()
        commitHash = curRef.commitHash()

        assert repoName == pin.pinned_to_repo

        repo = self.database.Repo.lookupAny(name=repoName)

        if repo:
            cur_commit = self.database.Commit.lookupAny(
                repo_and_hash=(repo, commitHash)
            )

            return (
                self.database.Branch.lookupAny(
                    reponame_and_branchname=(pin.pinned_to_repo, pin.pinned_to_branch)
                ),
                cur_commit,
            )

        return None, None

    def pinGetAllBranchPinsWatching(self, pin):
        """Find all the pins that would be updated automatically if this BranchPin object changed."""
        branch = self.pinGetPinnedToBranchAndCommit(pin)[0]
        if branch is None or branch.repo is None:
            return []

        return [x for x in self.database.BranchPin.lookupAll(branch=branch) if x.auto]

    def branchGetPinByRefname(self, branch, refname):
        for x in self.database.BranchPin.lookupAll(branch=branch):
            if x.repo_def == refname:
                return x

    def branchGetAllAutopins(self, branch):
        """Given a Branch, find all of its BranchPins that are auto."""
        return [x for x in self.database.BranchPin.lookupAll(branch=branch) if x.auto]

    def branchGetAllPins(self, branch):
        """Given a Branch, find all of its BranchPins"""
        return [x for x in self.database.BranchPin.lookupAll(branch=branch)]

    def branchGetAllBranchesAutopinnedToSelf(self, branch):
        """Get a list of all the branches that update if this Branch updates."""
        assert branch

        if branch.repo is None:
            return []

        return set(
            [
                branchPin.branch
                for branchPin in self.database.BranchPin.lookupAll(
                    pinned_to=(branch.repo.name, branch.branchname)
                )
                if branchPin.auto and branchPin.branch.repo is not None
            ]
        )

    def branchGetAllPinsAutopinnedToSelf(self, branch):
        """Get a list of all the branches that update if this Branch updates."""
        return set(
            [
                branchPin
                for branchPin in self.database.BranchPin.lookupAll(
                    pinned_to=(branch.repo.name, branch.branchname)
                )
                if branchPin.auto and branchPin.branch.repo is not None
            ]
        )

    def branchPinsAreCyclic(self, branch):
        """Given a Branch object, determine if updating it would produce a cycle of pin updates."""
        return GraphUtil.graphFindCycle(
            branch, self.branchGetAllBranchesAutopinnedToSelf
        )

    def pinGetCurrentAndDesiredCommit(self, pin):
        """Given a BranchPin object, get the current commit and desired commit."""
        branch, commit = self.pinGetPinnedToBranchAndCommit(pin)

        if branch is None:
            logging.error(
                "Pin %s/%s is pinned to a branch (%s/%s) that doesn't exist.",
                pin.branch.repo.name,
                pin.branch.branchname,
                pin.pinned_to_repo,
                pin.pinned_to_branch,
            )
            return None, None

        if branch.head is None:
            logging.error("Pin %s/%s is pinned to a branch (%s/%s) that has no HEAD.")
            return None, None

        return commit, branch.head

    def computePinUpdate(self, branch, specific_ref, intermediateCommits, isDownstream):
        """Compute the set of branches and pins to update. If "isDownstream" is true, 
        then 'branch' itself doesn't change - the pins around it do

        returns {branch: {pin: newCommit}}
        """
        if branch.head is None:
            return {}

        if isDownstream:
            # find all dirty autopins.
            pins = self.branchGetAllPinsAutopinnedToSelf(branch)

            logging.info(
                "Found the following branches pinned to %s: %s",
                branch.branchname,
                [
                    pin.branch.repo.name
                    + "/"
                    + pin.branch.branchname
                    + " as "
                    + pin.repo_def
                    for pin in pins
                ],
            )

            sourceCommits = {p: self.pinGetCurrentAndDesiredCommit(p)[0] for p in pins}
            desiredCommit = branch.head

            pinsWithSources = [p for p in pins if sourceCommits[p]]

            if (
                intermediateCommits
                and len(pinsWithSources) == len(pins)
                and len(set(sourceCommits.values())) == 1
            ):
                sourceCommit = list(sourceCommits.values())[0]

                if desiredCommit != sourceCommit:
                    chain = self.getFastForwardChain(sourceCommit, desiredCommit)

                    if chain:
                        desiredCommit = chain[-2]

            res = {}
            for p in pins:
                if p.branch not in res:
                    res[p.branch] = {}

                res[p.branch][p] = desiredCommit

            logging.info(
                "Updating the following branches %s",
                {b.branchname: {p.repo_def: res[b][p] for p in res[b]} for b in res},
            )

            return res
        else:
            if specific_ref is None:
                pins = self.branchGetAllPins(branch)
                if not pins:
                    return None
            else:
                assert not isDownstream
                pins = [self.branchGetPinByRefname(branch, specific_ref)]

                if not pins[0]:
                    return None

            assert not intermediateCommits

            res = {}

            for pin in pins:
                curCommit, desiredCommit = self.pinGetCurrentAndDesiredCommit(pin)
                if desiredCommit:
                    res[pin] = desiredCommit

            if res:
                return {branch: res}
            return None

    def getFastForwardChain(self, oldCommit, newCommit, max_commits=100):
        """Returns a list of commits in a sequence if newCommit is a direct fastforward of oldCommit

        newCommit will be first, oldcommit last.
        """
        assert newCommit != oldCommit

        chain = [newCommit]

        while chain[-1] != oldCommit:
            commit = chain[-1]

            if not commit.data or len(commit.data.parents) != 1:
                return None

            chain.append(commit.data.parents[0])

            if len(chain) > max_commits:
                return None

        return chain

    def findDownstreamBranchThatChanged(self, initBranch):
        """We may find that we're updating 'initBranch' because of a 
        downstream branch that changed."""

        for pin in self.branchGetAllAutopins(initBranch):
            underlying_branch, commit = self.pinGetPinnedToBranchAndCommit(pin)
            if (
                underlying_branch
                and underlying_branch.head
                and (commit and underlying_branch.head != commit or not commit)
            ):
                # this is the reason we're updating.
                return underlying_branch

        return None

    def updateBranchPin(
        self, branch, specific_ref=None, intermediateCommits=True, lookDownstream=True
    ):
        if lookDownstream:
            # We are updating because some underlying branch updated. We need to find it
            downstream_branch = self.findDownstreamBranchThatChanged(branch)
            if not downstream_branch:
                return False
            branch = downstream_branch

        cycle = self.branchPinsAreCyclic(branch)

        if cycle:
            logging.error(
                "Pin of Branch %s/%s is cyclic. Not updating. Cycle is:\n\n%s",
                branch.repo.name,
                branch.branchname,
                "\n".join([x.repo.name + "/" + x.branchname for x in cycle]),
            )
            return False

        # now compute a "pin update plan" consisting of a set of updates to the
        # pins in this one commit, each of which will be broadcast upstream.
        # pin_updates has the shape {branch: {repo_ref: new_commit}}
        try:
            pin_updates = self.computePinUpdate(
                branch, specific_ref, intermediateCommits, lookDownstream
            )
        except:
            if branch.repo is None:
                logging.error("Branch branch %s has no repo!", branch.branchname)
            else:
                logging.error(
                    "Failed to update pin of branch %s/%s",
                    branch.branchname,
                    branch.repo.name,
                )
            raise

        return self.applyPinUpdates(pin_updates, lookDownstream, branch)

    def applyPinUpdates(self, pin_updates, isDownstream, origBranch):
        if not pin_updates:
            return False

        new_hashes = self.executeSinglePinUpdate(pin_updates, isDownstream)

        for level in new_hashes:
            for b, new_hash in level:
                if new_hash:
                    self.branches_updated[b] = new_hash

        # now try to push these commits.
        logging.info(
            "Pushing %s commits due to update of %s/%s",
            len(self.branches_updated),
            origBranch.repo.name,
            origBranch.branchname,
        )

        for level in new_hashes:
            for subbranch, new_hash in level:
                if new_hash:
                    repo = self.source_control.getRepo(subbranch.repo.name)
                    if not repo.source_repo.pushCommit(new_hash, subbranch.branchname):
                        logging.error(
                            "Failed to push commit hash %s to %s/%s",
                            new_hash,
                            subbranch.repo.name,
                            subbranch.branchname,
                        )
                        return False
                    else:
                        logging.info(
                            "Successfully pushed commit hash %s to %s/%s",
                            new_hash,
                            subbranch.repo.name,
                            subbranch.branchname,
                        )

        return True

    def commitMessageFor(self, pin, commit):
        return (
            "New commit in pinned branch "
            + pin.pinned_to_repo
            + "/"
            + pin.pinned_to_branch
            + ":\n"
            + "\n".join(
                "    " + x
                for x in self.source_control.getRepo(commit.repo.name)
                .source_repo.standardCommitMessageFor(commit.hash)
                .split("\n")
            )
        )

    def executeSinglePinUpdate(self, initialPinsToUpdate, isDownstream):
        """Compute a set of commits that update a set of pins on a single branch.

        initialPinsToUpdate: {branch-> {pin: newCommit}}

        Returns [[(branch, new_hash)]] in order from upstream to downstream. Branches
        in the same sublist may be pushed simultaneously without creating a conflict.
        """
        if not initialPinsToUpdate:
            return []

        branches = set(initialPinsToUpdate)

        logging.info("Updating branches: %s", [b.branchname for b in branches])

        # now make a branch update ordering
        update_levels = GraphUtil.placeNodesInLevels(
            branches, self.branchGetAllBranchesAutopinnedToSelf
        )

        branch_new_hashes = {}

        if isDownstream:
            a_branch = list(branches)[0]
            a_pin = list(initialPinsToUpdate[a_branch])[0]

            standard_git_commit_message = self.commitMessageFor(
                a_pin, initialPinsToUpdate[a_branch][a_pin]
            )
        else:
            assert len(branches) == 1
            any_branch = sorted(branches)[0]

            standard_git_commit_message = "\n\n".join(
                [
                    self.commitMessageFor(pin, initialPinsToUpdate[any_branch][pin])
                    for pin in sorted(
                        initialPinsToUpdate[any_branch], key=lambda pin: pin.repo_def
                    )
                ]
            )

        for branch in update_levels[0]:
            logging.info("Checking root-level pin for branch %s", branch.branchname)

            assert (
                branch in initialPinsToUpdate
            ), "update level 0 should have only pins in our init set"

            for p in initialPinsToUpdate[branch]:
                assert p.branch == branch

            branch_new_hashes[branch] = self._updatePinsInCommitAndReturnHash(
                branch,
                {p: c.hash for p, c in initialPinsToUpdate[branch].items()},
                standard_git_commit_message,
            )

            if branch_new_hashes[branch] is None:
                # we didn't actually update this pin. Ignore it
                del branch_new_hashes[branch]
                logging.info("Branch %s is already up to date.", branch.branchname)

        # now update downstream levels
        level_ix = 1
        for level in update_levels[1:]:
            logging.info("Checking a level %s", level_ix)

            for branch_to_update in level:
                logging.info(
                    "Checking branch %s in level %s",
                    branch_to_update.branchname,
                    level_ix,
                )

                pins = self.branchGetAllAutopins(branch_to_update)

                pins_to_update = {
                    p: c.hash
                    for p, c in initialPinsToUpdate.get(branch_to_update, {}).items()
                }

                for pin in pins:
                    pinned_to_branch = self.pinGetPinnedToBranchAndCommit(pin)[0]
                    if pinned_to_branch and pinned_to_branch in branch_new_hashes:
                        pins_to_update[pin] = branch_new_hashes[pinned_to_branch]

                if pins_to_update:
                    branch_new_hashes[
                        branch_to_update
                    ] = self._updatePinsInCommitAndReturnHash(
                        branch_to_update, pins_to_update, standard_git_commit_message
                    )
                    if branch_new_hashes[branch_to_update] is None:
                        # we didn't need to update this
                        del branch_new_hashes[branch_to_update]
                        logging.info(
                            "Branch %s is already up to date.",
                            branch_to_update.branchname,
                        )

            level_ix += 1
        res = []
        for level in update_levels:
            res.append(
                [
                    (branch, branch_new_hashes[branch])
                    for branch in level
                    if branch in branch_new_hashes
                ]
            )

        return res

    def _updatePinsInCommitAndReturnHash(
        self, branch, pinToNewCommitHash, rootCommitMessage
    ):
        assert pinToNewCommitHash

        repo = self.source_control.getRepo(branch.repo.name)
        path = repo.source_repo.getTestDefinitionsPath(branch.head.hash)
        branchCommitHash = branch.head.hash

        if not path:
            logging.error(
                "Can't update pins of %s/%s because we can't find testDefinitions.yml",
                branch.repo.name,
                branch.branchname,
            )
            raise Exception("Couldn't update pin")

        contents = repo.source_repo.getFileContents(branchCommitHash, path)
        orig_contents = contents

        anyPinsApplied = False

        for pin, newCommitHash in pinToNewCommitHash.items():
            assert pin, "Branch %s/%s has no pin named %s" % (
                branch.repo.name,
                branch.branchname,
                pin.repo_def,
            )

            target_repo_name = pin.pinned_to_repo

            curCommitHash = pin.branch.head.data.repos[pin.repo_def].commitHash()

            if curCommitHash != newCommitHash:
                pat_text = r"\b({r})(\s*:\s*reference\s*:\s*)({tr}/{h})\b".format(
                    r=pin.repo_def, h=curCommitHash, tr=target_repo_name
                )

                pattern = re.compile(pat_text, flags=re.MULTILINE)

                new_contents = re.sub(
                    pattern,
                    (r"\1\2" + target_repo_name + "/" + newCommitHash),
                    contents,
                )

                if contents == new_contents:
                    logging.error(
                        "Failed to update %s/%s_updatePinsInCommitAndReturnHash (%s)'s reference to %s=%s/%s",
                        branch.repo.name,
                        branch.branchname,
                        branchCommitHash,
                        pin.repo_def,
                        target_repo_name,
                        curCommitHash,
                    )
                    raise Exception(
                        "Failed to update the pin in the testDefinitions file."
                    )

                anyPinsApplied = True
                contents = new_contents

        if not anyPinsApplied:
            return None

        assert orig_contents != contents

        new_commit_message = (
            "Updating pin%s %s:\n\n"
            % (
                "s" if len(pinToNewCommitHash) > 1 else "",
                ", ".join(sorted([x.repo_def for x in pinToNewCommitHash])),
            )
            + rootCommitMessage
        )

        new_hash = repo.source_repo.createCommit(
            branchCommitHash, {path: new_contents}, new_commit_message
        )

        assert new_hash

        return new_hash

    def _updatePinsByDefInCommitAndReturnHash(
        self, branch, origPins, pinRewrite, rootCommitMessage
    ):
        assert pinRewrite

        repo = self.source_control.getRepo(branch.repo.name)
        path = repo.source_repo.getTestDefinitionsPath(branch.head.hash)
        branchCommitHash = branch.head.hash

        if not path:
            logging.error(
                "Can't update pins of %s/%s because we can't find testDefinitions.yml",
                branch.repo.name,
                branch.branchname,
            )
            raise Exception("Couldn't update pin")

        contents = repo.source_repo.getFileContents(branchCommitHash, path)
        orig_contents = contents

        for pin, newPinVal in pinRewrite.items():
            contents = self.updatePinInContents(contents, pin, origPins[pin], newPinVal)

        new_hash = repo.source_repo.createCommit(
            branchCommitHash, {path: contents}, rootCommitMessage
        )

        assert new_hash

        return new_hash

    def updatePinInContents(self, contents, pin, curPinVal, newPinVal):
        pat_text = r"^\s*\b({r})(\s*:\s*reference\s*:\s*)({tr}/{h})\b".format(
            r=pin, tr=curPinVal.reponame(), h=curPinVal.commitHash()
        )

        pattern = re.compile(pat_text, flags=re.MULTILINE)

        offset = re.search(pattern, contents)

        if not offset:
            raise Exception("Failed to find the text location for pin %s" % pin)

        index = offset.start()
        subsequentLines = contents[index:].split("\n")

        def indentLevel(ln):
            return len(ln) - len(ln.lstrip())

        blockEnds = len(subsequentLines)
        for i in range(1, len(subsequentLines)):
            if indentLevel(subsequentLines[i]) <= indentLevel(subsequentLines[0]):
                blockEnds = i
                break

        yaml_identifier = re.compile(r"^[a-zA-Z0-9_/-]*$")

        def yamlQuoteIfNeeded(val):
            if yaml_identifier.match(val):
                return val
            else:
                return '"' + val.replace("\\", "\\\\").replace('"', '"') + '"'

        newVal = [
            yamlQuoteIfNeeded(pin) + ":"
        ]  # algebraic_to_json.encode_and_dump_as_yaml({pin:newPinVal})
        newVal.append("  reference: " + yamlQuoteIfNeeded(newPinVal.reference))
        newVal.append("  branch: " + yamlQuoteIfNeeded(newPinVal.branch))
        if newPinVal.auto:
            newVal.append("  auto: true")

        newVal = [" " * indentLevel(subsequentLines[0]) + line for line in newVal]

        subsequentLines[:blockEnds] = newVal

        finalContents = contents[:index] + "\n".join(subsequentLines)

        return finalContents
