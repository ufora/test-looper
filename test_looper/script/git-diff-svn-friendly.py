#!/usr/bin/python3

import test_looper.core.SubprocessRunner as SubprocessRunner
import sys

class DISCARD_CHUNK:
	pass

if len(sys.argv) != 3:
	print("Usage: git-diff-svn-friendly rev1 rev2")
	sys.exit(1)

result, lines = SubprocessRunner.callAndReturnResultAndMergedOutput(
	["git", "diff", "--no-color", "--no-renames"] + sys.argv[1:]
	)

if result != 0:
	print("\n".join(lines), file=sys.stderr)
	sys.exit(result)

def split_into_chunks(lines):
	chunks = []

	cur_chunk = []
	for l in lines:
		if l[:4] == 'diff':
			if cur_chunk:
				chunks.append(cur_chunk)
			
			cur_chunk = []

		cur_chunk.append(l)
	
	if cur_chunk:
		chunks.append(cur_chunk)

	return chunks

def make_svn_header(fname, src_revision, dest_srvision):
	return [
		"Index: %s" % fname,
		"===================================================================",
		"--- %s (%s)" % (fname, src_revision),
		"+++ %s (%s)" % (fname, dest_revision)
		]

def read_fname_from_git_chunk(chunk):
	assert chunk[0].startswith("diff --git ")

	full = chunk[0][len("diff --git "):]
	sz = (len(full)-1)/2

	fname1 = full[:sz]
	fname2 = full[sz+1:]

	assert fname1.startswith("a/"), (chunk[0], fname1)
	assert fname2.startswith("b/"), (chunk[0], fname2)

	fname1 = fname1[2:]
	fname2 = fname2[2:]

	assert fname1 == fname2, chunk[0]

	return fname1

def reformat_chunk(chunk, src_revision, dest_revision):
	fname = read_fname_from_git_chunk(chunk)

	if fname is DISCARD_CHUNK:
		return []

	for i in range(len(chunk)):
		if chunk[i].startswith("@@") or chunk[i].startswith("Binary files"):
			chunk[:i] = make_svn_header(fname, src_revision, dest_revision)
			return chunk

	if len(chunk) == 3:
		return make_svn_header(fname, src_revision, dest_revision)

	raise Exception("Invalid chunk: %s" % "\n".join(chunk[:6]))

def infer_revision(rev):
	result, output = SubprocessRunner.callAndReturnResultAndMergedOutput(["git","log","-n","1",rev])
	
	while output and output[-1].strip() == "":
		output.pop()

	revline = output[-1].strip()
	if revline.startswith("git-svn-id"):
		index = revline.find("@")
		if index >= 0:
			revline = revline[index+1:]
			index2 = revline.find(" ")
			if index2 >= 0:
				revline = revline[:index2]

				return "revision %s" % revline

	return "working copy"

chunks = split_into_chunks(lines)

src_revision = infer_revision(sys.argv[1])
dest_revision = infer_revision(sys.argv[2])

for c in chunks:
	reformat_chunk(c, src_revision, dest_revision)

print("\n".join(["\n".join(lines) for lines in chunks]))
