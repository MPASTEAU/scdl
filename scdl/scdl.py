#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

"""scdl allows you to download music from Soundcloud

Usage:
    scdl -l <track_url> [-a | -f | -C | -t | -p | -r][-c | --force-metadata][-n <maxtracks>]
[-o <offset>][--hidewarnings][--debug | --error][--path <path>][--addtofile][--addtimestamp]
[--onlymp3][--hide-progress][--min-size <size>][--max-size <size>][--remove][--no-album-tag]
[--no-playlist-folder][--download-archive <file>][--extract-artist][--flac][--original-art]
[--original-name][--no-original][--only-original][--name-format <format>]
[--playlist-name-format <format>][--client-id <id>][--auth-token <token>][--overwrite]
    scdl -h | --help
    scdl --version


Options:
    -h --help                       Show this screen
    --version                       Show version
    -l [url]                        URL can be track/playlist/user
    -n [maxtracks]                  Download the n last tracks of a playlist according to the creation date
    -s                              Download the stream of a user (token needed)
    -a                              Download all tracks of user (including reposts)
    -t                              Download all uploads of a user (no reposts)
    -f                              Download all favorites of a user
    -C                              Download all commented by a user
    -p                              Download all playlists of a user
    -r                              Download all reposts of user
    -c                              Continue if a downloaded file already exists
    --force-metadata                This will set metadata on already downloaded track
    -o [offset]                     Begin with a custom offset
    --addtimestamp                  Add track creation timestamp to filename,
                                    which allows for chronological sorting
    --addtofile                     Add artist to filename if missing
    --debug                         Set log level to DEBUG
    --download-archive [file]       Keep track of track IDs in an archive file,
                                    and skip already-downloaded files
    --error                         Set log level to ERROR
    --extract-artist                Set artist tag from title instead of username
    --hide-progress                 Hide the wget progress bar
    --hidewarnings                  Hide Warnings. (use with precaution)
    --max-size [max-size]           Skip tracks larger than size (k/m/g)
    --min-size [min-size]           Skip tracks smaller than size (k/m/g)
    --no-playlist-folder            Download playlist tracks into main directory,
                                    instead of making a playlist subfolder
    --onlymp3                       Download only the streamable mp3 file,
                                    even if track has a Downloadable file
    --path [path]                   Use a custom path for downloaded files
    --remove                        Remove any files not downloaded from execution
    --flac                          Convert original files to .flac
    --no-album-tag                  On some player track get the same cover art if from the same album, this prevent it
    --original-art                  Download original cover art
    --original-name                 Do not change name of original file downloads
    --no-original                   Do not download original file; only mp3 or m4a
    --only-original                 Only download songs with original file available
    --name-format [format]          Specify the downloaded file name format
    --playlist-name-format [format] Specify the downloaded file name format, if it is being downloaded as part of a playlist
    --client-id [id]                Specify the client_id to use
    --auth-token [token]            Specify the auth token to use
    --overwrite                     Overwrite file if it already exists
"""

import configparser
import logging
import mimetypes
import pathlib

mimetypes.init()

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from dataclasses import asdict

import mutagen
from mutagen.easymp4 import EasyMP4

EasyMP4.RegisterTextKey("website", "purl")
import requests
from clint.textui import progress
from docopt import docopt
from pathvalidate import sanitize_filename
from soundcloud import BasicAlbumPlaylist, BasicTrack, MiniTrack, SoundCloud

from scdl import __version__, utils

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addFilter(utils.ColorizeFilter())

fileToKeep = []


def main():
    """
    Main function, parses the URL from command line arguments
    """
    signal.signal(signal.SIGINT, signal_handler)

    # Parse arguments
    arguments = docopt(__doc__, version=__version__)
    python_args = {
        "offset": 1
    }

    if arguments["--debug"]:
        logger.level = logging.DEBUG
    elif arguments["--error"]:
        logger.level = logging.ERROR
        
    if "XDG_CONFIG_HOME" in os.environ:
        config_file = pathlib.Path(os.environ["XDG_CONFIG_HOME"], "scdl", "scdl.cfg")
    else:
        config_file = pathlib.Path.home().joinpath(".config", "scdl", "scdl.cfg")

    # import conf file
    config = get_config(config_file)
    
    # change download path
    path = config["scdl"]["path"]
    if os.path.exists(path):
        os.chdir(path)
    else:
        logger.error(f"Invalid download path '{path}' in {config_file}")
        sys.exit(-1)
    
    logger.info("Soundcloud Downloader")
    logger.debug(arguments)

    if not arguments["--client-id"]:
        arguments["--client-id"] = config["scdl"]["client_id"]

    if not arguments["--auth-token"]:
        arguments["--auth-token"] = config["scdl"]["auth_token"]
    
    client_id, token = arguments["--client-id"], arguments["--auth-token"]
    
    client = SoundCloud(client_id, token if token else None)
    
    if not client.is_client_id_valid():
        raise ValueError(f"client_id is not valid")
    
    if token and not client.is_auth_token_valid():
        raise ValueError(f"auth_token is not valid")

    if arguments["-o"] is not None:
        try:
            python_args["offset"] = int(arguments["-o"])
            if python_args["offset"] < 1:
                raise ValueError()
        except:
            logger.error("Offset should be a positive integer...")
            sys.exit(-1)
        logger.debug("offset: %d", python_args["offset"])

    if arguments["--min-size"] is not None:
        try:
            arguments["--min-size"] = utils.size_in_bytes(arguments["--min-size"])
        except:
            logger.exception(
                "Min size should be an integer with a possible unit suffix"
            )
            sys.exit(-1)
        logger.debug("min-size: %d", arguments["--min-size"])

    if arguments["--max-size"] is not None:
        try:
            arguments["--max-size"] = utils.size_in_bytes(arguments["--max-size"])
        except:
            logger.error("Max size should be an integer with a possible unit suffix")
            sys.exit(-1)
        logger.debug("max-size: %d", arguments["--max-size"])

    if arguments["--hidewarnings"]:
        warnings.filterwarnings("ignore")

    if arguments["--path"] is not None:
        if os.path.exists(arguments["--path"]):
            os.chdir(arguments["--path"])
        else:
            logger.error("Invalid path in arguments...")
            sys.exit(-1)
    logger.debug("Downloading to " + os.getcwd() + "...")
    
    if not arguments["--name-format"]:
        arguments["--name-format"] = config["scdl"]["name_format"]
    
    if not arguments["--playlist-name-format"]:
        arguments["--playlist-name-format"] = config["scdl"]["playlist_name_format"]
        
    # convert arguments dict to python_args (kwargs-friendly args)
    for key, value in arguments.items():
        key = key.strip("-").replace("-", "_")
        python_args[key] = value

    if arguments["-l"]:
        download_url(client, **python_args)

    if arguments["--remove"]:
        remove_files()

def get_config(config_file: pathlib.Path) -> configparser.ConfigParser:
    """
    Gets config from scdl.cfg
    """
    config = configparser.ConfigParser()
    
    default_config_file = pathlib.Path(__file__).with_name("scdl.cfg")

    # load default config first
    config.read(default_config_file)
    
    # load config file if it exists
    if config_file.exists():
        config.read(config_file)
    
    # save config to disk
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="UTF-8") as f:
        config.write(f)
        
    return config


def download_url(client: SoundCloud, **kwargs):
    """
    Detects if a URL is a track or a playlist, and parses the track(s)
    to the track downloader
    """
    url = kwargs.get("l")
    item = client.resolve(url)
    logger.debug(item)
    if not item:
        return
    elif item.kind == "track":
        logger.info("Found a track")
        download_track(client, item, **kwargs)
    elif item.kind == "playlist":
        logger.info("Found a playlist")
        download_playlist(client, item, **kwargs)
    elif item.kind == "user":
        user = item
        logger.info("Found a user profile")
        if kwargs.get("f"):
            logger.info(f"Retrieving all likes of user {user.username}...")
            resources = client.get_user_likes(user.id, limit=1000)
            for i, like in enumerate(resources, 1):
                logger.info(f"like n°{i} of {user.likes_count}")
                if hasattr(like, "track"):
                    download_track(client, like.track, **kwargs)
                elif hasattr(like, "playlist"):
                    download_playlist(client, client.get_playlist(like.playlist.id), **kwargs)
                else:
                    raise ValueError(f"Unknown like type {like}")
            logger.info(f"Downloaded all likes of user {user.username}!")
        elif kwargs.get("C"):
            logger.info(f"Retrieving all commented tracks of user {user.username}...")
            resources = client.get_user_comments(user.id, limit=1000)
            for i, comment in enumerate(resources, 1):
                logger.info(f"comment n°{i} of {user.comments_count}")
                download_track(client, client.get_track(comment.track.id), **kwargs)
            logger.info(f"Downloaded all commented tracks of user {user.username}!")
        elif kwargs.get("t"):
            logger.info(f"Retrieving all tracks of user {user.username}...")
            resources = client.get_user_tracks(user.id, limit=1000)
            for i, track in enumerate(resources, 1):
                logger.info(f"track n°{i} of {user.track_count}")
                download_track(client, track, **kwargs)
            logger.info(f"Downloaded all tracks of user {user.username}!")
        elif kwargs.get("a"):
            logger.info(f"Retrieving all tracks & reposts of user {user.username}...")
            resources = client.get_user_stream(user.id, limit=1000)
            for i, item in enumerate(resources, 1):
                logger.info(f"item n°{i} of {user.track_count + user.reposts_count if user.reposts_count else '?'}")
                if item.type in ("track", "track-repost"):
                    download_track(client, item.track, **kwargs)
                elif item.type in ("playlist", "playlist-repost"):
                    download_playlist(client, item.playlist, **kwargs)
                else:
                    raise ValueError(f"Unknown item type {item.type}")
            logger.info(f"Downloaded all tracks & reposts of user {user.username}!")
        elif kwargs.get("p"):
            logger.info(f"Retrieving all playlists of user {user.username}...")
            resources = client.get_user_playlists(user.id, limit=1000)
            for i, playlist in enumerate(resources, 1):
                logger.info(f"playlist n°{i} of {user.playlist_count}")
                download_playlist(client, playlist, **kwargs)
            logger.info(f"Downloaded all playlists of user {user.username}!")
        elif kwargs.get("r"):
            logger.info(f"Retrieving all reposts of user {user.username}...")
            resources = client.get_user_reposts(user.id, limit=1000)
            for i, item in enumerate(resources, 1):
                logger.info(f"item n°{i} of {user.reposts_count or '?'}")
                if item.type == "track-repost":
                    download_track(client, item.track, **kwargs)
                elif item.type == "playlist-repost":
                    download_playlist(client, item.playlist, **kwargs)
                else:
                    raise ValueError(f"Unknown item type {item.type}")
            logger.info(f"Downloaded all reposts of user {user.username}!")
        else:
            logger.error("Please provide a download type...")
    else:
        logger.error("Unknown item type {0}".format(item.kind))

def remove_files():
    """
    Removes any pre-existing tracks that were not just downloaded
    """
    logger.info("Removing local track files that were not downloaded...")
    files = [f for f in os.listdir(".") if os.path.isfile(f)]
    for f in files:
        if f not in fileToKeep:
            os.remove(f)

def download_playlist(client: SoundCloud, playlist: BasicAlbumPlaylist, **kwargs):
    """
    Downloads a playlist
    """
    playlist_name = playlist.title.encode("utf-8", "ignore")
    playlist_name = playlist_name.decode("utf8")
    playlist_name = sanitize_filename(playlist_name)

    if not kwargs.get("no_playlist_folder"):
        if not os.path.exists(playlist_name):
            os.makedirs(playlist_name)
        os.chdir(playlist_name)

    try:
        if kwargs.get("n"):  # Order by creation date and get the n lasts tracks
            playlist.tracks.sort(
                key=lambda track: track.created_at, reverse=True
            )
            playlist.tracks = playlist.tracks[: int(kwargs.get("n"))]
        else:
            del playlist.tracks[: kwargs.get("offset") - 1]
        tracknumber_digits = len(str(len(playlist.tracks)))
        for counter, track in enumerate(playlist.tracks, kwargs.get("offset")):
            logger.debug(track)
            logger.info(f"Track n°{counter}")
            playlist_info = {
                "author": playlist.user.username,
                "title": playlist.title,
                "tracknumber": str(counter).zfill(tracknumber_digits),
            }
            if isinstance(track, MiniTrack):
                track = client.get_track(track.id)
            download_track(client, track, playlist_info, **kwargs)
    finally:
        if not kwargs.get("no_playlist_folder"):
            os.chdir("..")

def try_utime(path, filetime):
    try:
        os.utime(path, (time.time(), filetime))
    except:
        logger.error("Cannot update utime of file")

def get_filename(track: BasicTrack, original_filename=None, aac=False, playlist_info=None, **kwargs):
    
    if kwargs.get("original_name") and original_filename:
        return original_filename
    
    username = track.user.username
    title = track.title.encode("utf-8", "ignore").decode("utf-8")

    if kwargs.get("addtofile"):
        if username not in title and "-" not in title:
            title = "{0} - {1}".format(username, title)
            logger.debug('Adding "{0}" to filename'.format(username))

    timestamp = str(int(track.created_at.timestamp()))
    if kwargs.get("addtimestamp"):
        title = timestamp + "_" + title
    
    if not kwargs.get("addtofile") and not kwargs.get("addtimestamp"):
        if playlist_info:
            title = kwargs.get("playlist_name_format").format(**asdict(track), playlist=playlist_info, timestamp=timestamp)
        else:
            title = kwargs.get("name_format").format(**asdict(track), timestamp=timestamp)

    ext = ".m4a" if aac else ".mp3"  # contain aac in m4a to write metadata
    if original_filename is not None:
        original_filename.encode("utf-8", "ignore").decode("utf8")
        ext = os.path.splitext(original_filename)[1]
    # get filename to 255 bytes
    while len(title.encode("utf-8")) > 255 - len(ext.encode("utf-8")):
        title = title[:-1]
    filename = title + ext.lower()
    filename = sanitize_filename(filename)
    return filename


def download_original_file(client: SoundCloud, track: BasicTrack, title: str, playlist_info=None, **kwargs):
    logger.info("Downloading the original file.")

    # Get the requests stream
    url = client.get_track_original_download(track.id)
    
    if not url:
        logger.info("Could not get original download link")
        return (None, False)
    
    r = requests.get(url, stream=True)
    if r.status_code == 401:
        logger.info("The original file has no download left.")
        return (None, False)

    if r.status_code == 404:
        logger.info("Could not get name from stream - using basic name")
        return (None, False)

    # Find filename
    d = r.headers.get("content-disposition")
    filename = re.findall("filename=(.+)", d)[0]
    filename, ext = os.path.splitext(filename)

    # Find file extension
    mime = r.headers.get("content-type")
    ext = mimetypes.guess_extension(mime) or ext
    filename += ext

    filename = get_filename(track, filename, playlist_info=playlist_info, **kwargs)
    logger.debug(f"filename : {filename}")

    # Skip if file ID or filename already exists
    if already_downloaded(track, title, filename, **kwargs):
        if kwargs.get("flac") and can_convert(filename):
            filename = filename[:-4] + ".flac"
        return (filename, True)

    # Write file
    total_length = int(r.headers.get("content-length"))
    temp = tempfile.NamedTemporaryFile(delete=False)
    received = 0
    with temp as f:
        for chunk in progress.bar(
            r.iter_content(chunk_size=1024),
            expected_size=(total_length / 1024) + 1,
            hide=True if kwargs.get("hide_progress") else False,
        ):
            if chunk:
                received += len(chunk)
                f.write(chunk)
                f.flush()

    if received != total_length:
        logger.error("connection closed prematurely, download incomplete")
        sys.exit(-1)

    shutil.move(temp.name, os.path.join(os.getcwd(), filename))
    if kwargs.get("flac") and can_convert(filename):
        logger.info("Converting to .flac...")
        newfilename = filename[:-4] + ".flac"

        commands = ["ffmpeg", "-i", filename, newfilename, "-loglevel", "error"]
        logger.debug(f"Commands: {commands}")
        subprocess.call(commands)
        os.remove(filename)
        filename = newfilename

    return (filename, False)


def get_track_m3u8(client: SoundCloud, track: BasicTrack, aac=False):
    url = None
    for transcoding in track.media.transcodings:
        if transcoding.format.protocol == "hls":
            if (not aac and transcoding.format.mime_type == "audio/mpeg") or (
                aac and transcoding.format.mime_type.startswith("audio/mp4")
            ):
                url = transcoding.url

    if url is not None:
        headers = client.get_default_headers()
        if client.auth_token:
            headers["Authorization"] = f"OAuth {client.auth_token}"
        r = requests.get(url, params={"client_id": client.client_id}, headers=headers)
        logger.debug(r.url)
        return r.json()["url"]


def download_hls(client: SoundCloud, track: BasicTrack, title: str, playlist_info=None, **kwargs):

    if kwargs["onlymp3"]:
        aac = False
    else:
        aac = any(
            t.format.mime_type.startswith("audio/mp4")
            for t in track.media.transcodings
        )

    filename = get_filename(track, None, aac, playlist_info, **kwargs)
    logger.debug(f"filename : {filename}")
    # Skip if file ID or filename already exists
    if already_downloaded(track, title, filename, **kwargs):
        return (filename, True)

    # Get the requests stream
    url = get_track_m3u8(client, track, aac)
    filename_path = os.path.abspath(filename)

    p = subprocess.run(
        ["ffmpeg", "-i", url, "-c", "copy", filename_path, "-loglevel", "error"],
        capture_output=True,
    )
    if p.stderr:
        logger.error(p.stderr.decode("utf-8"))
    return (filename, False)


def download_track(client: SoundCloud, track: BasicTrack, playlist_info=None, **kwargs):
    """
    Downloads a track
    """
    title = track.title
    title = title.encode("utf-8", "ignore").decode("utf8")
    logger.info(f"Downloading {title}")

    # Not streamable
    if not track.streamable:
        logger.error(f"{title} is not streamable...")
        return

    # Geoblocked track
    if track.policy == "BLOCK":
        logger.error(f"{title} is not available in your location...\n")
        return

    # Downloadable track
    filename = None
    is_already_downloaded = False
    if (
        track.downloadable
        and track.has_downloads_left
        and not kwargs["onlymp3"]
        and not kwargs.get("no_original")
    ):
        filename, is_already_downloaded = download_original_file(client, track, title, playlist_info, **kwargs)

    if filename is None:
        if kwargs.get("only_original"):
            logger.info(f'Track "{title}" does not have original file available. Skipping...')
            return
        filename, is_already_downloaded = download_hls(client, track, title, playlist_info, **kwargs)

    if kwargs.get("remove"):
        fileToKeep.append(filename)

    record_download_archive(track, **kwargs)

    # Skip if file ID or filename already exists
    if is_already_downloaded and not kwargs.get("force_metadata"):
        logger.info(f'Track "{title}" already downloaded.')
        return

    # If file does not exist an error occurred
    if not os.path.isfile(filename):
        logger.error(f"An error occurred downloading {filename}.\n")
        logger.error("Exiting...")
        sys.exit(-1)

    # Try to set the metadata
    if (
        filename.endswith(".mp3")
        or filename.endswith(".flac")
        or filename.endswith(".m4a")
    ):
        try:
            set_metadata(track, filename, playlist_info, **kwargs)
        except:
            logger.exception("Error trying to set the tags...")
    else:
        logger.error("This type of audio doesn't support tagging...")

    # Try to change the real creation date
    filetime = int(time.mktime(track.created_at.timetuple()))
    try_utime(filename, filetime)

    logger.info(f"{filename} Downloaded.\n")


def can_convert(filename):
    ext = os.path.splitext(filename)[1]
    return "wav" in ext or "aif" in ext


def already_downloaded(track: BasicTrack, title: str, filename: str, **kwargs):
    """
    Returns True if the file has already been downloaded
    """
    already_downloaded = False

    if os.path.isfile(filename):
        already_downloaded = True
        if kwargs.get("overwrite"):
            os.remove(filename)
            already_downloaded = False
    if (
        kwargs.get("flac")
        and can_convert(filename)
        and os.path.isfile(filename[:-4] + ".flac")
    ):
        already_downloaded = True
        if kwargs.get("overwrite"):
            os.remove(filename[:-4] + ".flac")
            already_downloaded = False
    if kwargs.get("download_archive") and in_download_archive(track, **kwargs):
        already_downloaded = True

    if kwargs.get("flac") and can_convert(filename) and os.path.isfile(filename):
        already_downloaded = False

    if already_downloaded:
        if kwargs.get("c") or kwargs.get("remove") or kwargs.get("force_metadata"):
            return True
        else:
            logger.error(f'Track "{title}" already exists!')
            logger.error("Exiting... (run again with -c to continue)")
            sys.exit(-1)
    return False


def in_download_archive(track: BasicTrack, **kwargs):
    """
    Returns True if a track_id exists in the download archive
    """
    if not kwargs.get("download_archive"):
        return

    archive_filename = kwargs.get("download_archive")
    try:
        with open(archive_filename, "a+", encoding="utf-8") as file:
            file.seek(0)
            track_id = str(track.id)
            for line in file:
                if line.strip() == track_id:
                    return True
    except IOError as ioe:
        logger.error("Error trying to read download archive...")
        logger.error(ioe)

    return False


def record_download_archive(track: BasicTrack, **kwargs):
    """
    Write the track_id in the download archive
    """
    if not kwargs.get("download_archive"):
        return

    archive_filename = kwargs.get("download_archive")
    try:
        with open(archive_filename, "a", encoding="utf-8") as file:
            file.write(f"{track.id}\n")
    except IOError as ioe:
        logger.error("Error trying to write to download archive...")
        logger.error(ioe)


def set_metadata(track: BasicTrack, filename: str, playlist_info=None, **kwargs):
    """
    Sets the mp3 file metadata using the Python module Mutagen
    """
    logger.info("Setting tags...")
    artwork_url = track.artwork_url
    user = track.user
    if not artwork_url:
        artwork_url = user.avatar_url
    response = None
    if kwargs.get("original_art"):
        new_artwork_url = artwork_url.replace("large", "original")
        try:
            response = requests.get(new_artwork_url, stream=True)
            if response.headers["Content-Type"] not in (
                "image/png",
                "image/jpeg",
                "image/jpg",
            ):
                response = None
        except:
            pass
    if response is None:
        new_artwork_url = artwork_url.replace("large", "t500x500")
        response = requests.get(new_artwork_url, stream=True)
        if response.headers["Content-Type"] not in (
            "image/png",
            "image/jpeg",
            "image/jpg",
        ):
            response = None
    if response is None:
        logger.error(f"Could not get cover art at {new_artwork_url}")
    with tempfile.NamedTemporaryFile() as out_file:
        if response:
            shutil.copyfileobj(response.raw, out_file)
            out_file.seek(0)

        track.date = track.created_at.strftime("%Y-%m-%d %H::%M::%S")

        track.artist = user.username
        if kwargs.get("extract_artist"):
            for dash in [" - ", " − ", " – ", " — ", " ― "]:
                if dash in track.title:
                    artist_title = track.title.split(dash)
                    track.artist = artist_title[0].strip()
                    track.title = artist_title[1].strip()
                    break

        audio = mutagen.File(filename, easy=True)
        audio.delete()
        audio["title"] = track.title
        audio["artist"] = track.artist
        if track.genre:
            audio["genre"] = track.genre
        if track.permalink_url:
            audio["website"] = track.permalink_url
        if track.date:
            audio["date"] = track.date
        if playlist_info:
            if not kwargs.get("no_album_tag"):
                audio["album"] = playlist_info["title"]
            audio["tracknumber"] = str(playlist_info["tracknumber"])

        audio.save()

        a = mutagen.File(filename)
        if track.description:
            if a.__class__ == mutagen.flac.FLAC:
                a["description"] = track.description
            elif a.__class__ == mutagen.mp3.MP3:
                a["COMM"] = mutagen.id3.COMM(
                    encoding=3, lang="ENG", text=track.description
                )
            elif a.__class__ == mutagen.mp4.MP4:
                a["\xa9cmt"] = track.description
        if response:
            if a.__class__ == mutagen.flac.FLAC:
                p = mutagen.flac.Picture()
                p.data = out_file.read()
                p.mime = "image/jpeg"
                p.type = mutagen.id3.PictureType.COVER_FRONT
                a.add_picture(p)
            elif a.__class__ == mutagen.mp3.MP3:
                a["APIC"] = mutagen.id3.APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=out_file.read(),
                )
            elif a.__class__ == mutagen.mp4.MP4:
                a["covr"] = [mutagen.mp4.MP4Cover(out_file.read())]
        a.save()


def signal_handler(signal, frame):
    """
    Handle keyboard interrupt
    """
    logger.info("\nGood bye!")
    sys.exit(0)


def is_ffmpeg_available():
    """
    Returns true if ffmpeg is available in the operating system
    """
    return shutil.which("ffmpeg") is not None


if __name__ == "__main__":
    main()
