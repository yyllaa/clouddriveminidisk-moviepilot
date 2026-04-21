# clouddriveminidisk-moviepilot

MoviePilot V2 plugin repository for `CloudDriveMiniDisk`.

## Structure

- `package.v2.json`
- `icons/Cloudrive_A.png`
- `plugins.v2/clouddriveminidisk`

## Purpose

This plugin uses the `clouddrive-mini` project's HTTP API as a custom
MoviePilot storage backend.

## Main Capabilities

- browse
- detail
- mkdir
- delete
- rename
- download
- chunked upload
- copy
- move
- storage usage

## Plugin Source

See:

- `plugins.v2/clouddriveminidisk/__init__.py`
- `plugins.v2/clouddriveminidisk/clouddrive_mini_api.py`

## Notes

- The plugin icon uses the local repository file `icons/Cloudrive_A.png`.
- Real runtime testing in MoviePilot is still required.
