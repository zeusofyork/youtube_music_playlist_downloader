#!/usr/bin/env python3
# YouTube Music Playlist Downloader
version = "1.2.3"

import os
import re
import json
import time
import requests
import subprocess
from PIL import Image
from io import BytesIO
from pathlib import Path
from yt_dlp import YoutubeDL, postprocessor
from urllib.parse import urlparse, parse_qs
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TRCK, TALB, TDRC, WOAR, error

# ID3 info:
# APIC: thumbnail
# TIT2: title
# TPE1: artist
# TRCK: track number
# TALB: album
# TDRC: upload date
# WOAR: link

class FilePathCollector(postprocessor.common.PostProcessor):
    def __init__(self):
        super(FilePathCollector, self).__init__(None)
        self.file_paths = []

    def run(self, information):
        self.file_paths.append(information['filepath'])
        return [], information

def write_config(file, config: dict):
    with open(file, "w") as f:
        json.dump(config, f, indent=4)

def check_ffmpeg():
    ffmpeg_available = True
    try:
        subprocess.check_output(['ffmpeg', '-version'])
    except Exception as e:
        ffmpeg_available = False
    if not ffmpeg_available:
        print("\n".join([
            "[ERROR] ffmpeg not found. Please ensure ffmpeg is installed",
            "and you have included it in your PATH environment variable.",
            "Download ffmpeg here: https://www.ffmpeg.org/download.html.",
            "-----------------------------------------------------------",
        ]))
    return ffmpeg_available

def get_playlist_info(config: dict):
    ytdl_opts = {
        "quiet": True,
        "geo_bypass": True,
        "dump_single_json": True,
        "extract_flat": True,
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "playlistreverse": config["reverse_playlist"]
    }
    with YoutubeDL(ytdl_opts) as ytdl:
        info_dict = ytdl.extract_info(config["url"], download=False)

    return info_dict

def convert_image_type(image, image_type):
    with BytesIO() as f:
        image.convert("RGB").save(f, format=image_type)
        return f.getvalue()

def update_track_num(file_path, track_num):
    tags = ID3(file_path)
    tags.add(TRCK(encoding=3, text=str(track_num)))

    tags.save(v2_version=3)

def get_metadata_map():
    return {
        "title": "TIT2",
        "cover": "APIC:Front cover",
        "track": "TRCK",
        "artist": "TPE1",
        "album": "TALB",
        "date": "TDRC",
        "url": "WOAR"
    }

def get_metadata_dict(tags):
    return {tag:tags.getall(tag) for tag in get_metadata_map().values()}

def valid_metadata(config, metadata_dict):
    include_metadata = config["include_metadata"].copy()

    # WOAR URL is required to identify video
    include_metadata["url"] = True

    selected_tags = [value for key, value in get_metadata_map().items() if include_metadata[key]]
    return all([value for tag, value in metadata_dict.items() if tag in selected_tags])

def get_song_info_ytdl(track_num, config: dict):
    # Get ytdl for song info
    name_format = config["name_format"]
    if config["track_num_in_name"]:
        name_format = f"{track_num}. {name_format}"

    ytdl_opts = {
        "quiet": True,
        "geo_bypass": True,
        "outtmpl": name_format,
        "format": config["audio_format"],
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": config["audio_codec"],
            "preferredquality": config["audio_quality"],
        }]
    }

    info_dict = {}
    return YoutubeDL(ytdl_opts)

def get_song_info(track_num, link, config: dict):
    # Get song metadata from youtube
    ytdl = get_song_info_ytdl(track_num, config)
    return ytdl.extract_info(link, download=False)

def generate_metadata(file_path, link, track_num, playlist_name, config: dict, regenerate_metadata: bool, force_update: bool):
    try:
        tags = ID3(file_path)
    except:
        # Unsupported audio codec for metadata
        force_update_file_name = ""
        if force_update:
            try:
                info_dict = get_song_info(track_num, link, config)
                info_dict_with_audio_ext = dict(info_dict)
                info_dict_with_audio_ext["ext"] = config["audio_codec"]
                force_update_file_name = get_song_info_ytdl(track_num, config).prepare_filename(info_dict_with_audio_ext)
            except Exception as e:
                raise Exception(f"Unable to gather information for updated file name: {e}")
        return force_update_file_name

    # Generate only if metadata is missing or if explicitly flagged
    metadata_dict = get_metadata_dict(tags)

    force_update_file_name = ""
    if force_update:
        for tag in metadata_dict.keys():
            if tag != "WOAR":
                # WOAR URL is required to identify video
                tags.delall(tag)
                metadata_dict[tag] = []

    if regenerate_metadata or force_update or not valid_metadata(config, metadata_dict):
        try:
            info_dict = get_song_info(track_num, link, config)

            if force_update:
                info_dict_with_audio_ext = dict(info_dict)
                info_dict_with_audio_ext["ext"] = config["audio_codec"]
                force_update_file_name = get_song_info_ytdl(track_num, config).prepare_filename(info_dict_with_audio_ext)

            thumbnail = info_dict.get("thumbnail")
            upload_date = info_dict.get("upload_date")
            title = info_dict.get("title")
            track = info_dict.get("track")
            uploader = info_dict.get("uploader")
            artist = info_dict.get("artist")
            album = info_dict.get("album")
        except Exception as e:
            raise Exception(f"Unable to gather information for song metadata: {e}")

        try:
            # Generate tags
            print(f"Updating metadata for '{title}'...")
            include_metadata = config["include_metadata"]

            # These tags will not be regenerated in case of config changes
            if not metadata_dict["APIC:Front cover"] and include_metadata["cover"]:
                # Generate thumbnail
                img = Image.open(requests.get(thumbnail, stream=True).raw)

                # Ensure aspect ratio
                target_ratio = [16, 9]
                width, height = img.size
                width_ratio = width / target_ratio[0]
                height_ratio = height / target_ratio[1]
                if width_ratio > height_ratio:
                    half_width = width / 2
                    min_offset = (height_ratio * target_ratio[0]) / 2
                    left = half_width - min_offset
                    right = half_width + min_offset
                    img = img.crop([left, 0, right, height])
                elif height_ratio > width_ratio:
                    half_height = height / 2
                    min_offset = (width_ratio * target_ratio[1]) / 2
                    top = half_height - min_offset
                    bottom = half_height + min_offset
                    img = img.crop([0, top, width, bottom])

                # Crop to square
                width, height = img.size
                half_width = width / 2
                half_height = height / 2
                min_offset = min(half_width, half_height)
                left = half_width - min_offset
                right = half_width + min_offset
                top = half_height - min_offset
                bottom = half_height + min_offset
                img_data = convert_image_type(img.crop([left, top, right, bottom]), config["image_format"])
                tags.add(APIC(3, f"image/{config['image_format']}", 3, "Front cover", img_data))

            if not metadata_dict["TRCK"] and include_metadata["track"]:
                tags.add(TRCK(encoding=3, text=str(track_num)))

            if not metadata_dict["TDRC"] and include_metadata["date"]:
                tags.add(TDRC(encoding=3, text=time.strftime('%Y-%m-%d', time.strptime(upload_date, '%Y%m%d'))))

            if not metadata_dict["WOAR"]:
                tags.add(WOAR(link))

            # These tags can be regenerated in case of config changes
            if include_metadata["title"]:
                if config["use_title"] or track is None:
                    tags.add(TIT2(encoding=3, text=title))
                else:
                    tags.add(TIT2(encoding=3, text=track))

            if include_metadata["artist"]:
                if config["use_uploader"] or artist is None:
                    tags.add(TPE1(encoding=3, text=uploader))
                else:
                    tags.add(TPE1(encoding=3, text=artist))

            if include_metadata["album"]:
                if config["use_playlist_name"]:
                    tags.add(TALB(encoding=3, text=playlist_name))
                elif album is not None:
                    tags.add(TALB(encoding=3, text=album))
                else:
                    tags.add(TALB(encoding=3, text="Unknown Album"))

            tags.save(v2_version=3)
        except Exception as e:
            raise Exception(f"Unable to update song metadata: {e}")

    return force_update_file_name

def download_song(link, playlist_name, track_num, config: dict):
    directory = os.path.join(os.getcwd(), playlist_name)
    name_format = config["name_format"]
    if config["track_num_in_name"]:
        name_format = f"{track_num}. {name_format}"

    ytdl_opts = {
        "outtmpl": f"{directory}/{name_format}",
        "ignoreerrors": True,
        "format": config["audio_format"],
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": config["audio_codec"],
            "preferredquality": config["audio_quality"],
        }],
        "geo_bypass": True
    }

    if not config["verbose"]:
        ytdl_opts["quiet"] = True
        ytdl_opts["external_downloader_args"] = ["-loglevel", "panic"]

    with YoutubeDL(ytdl_opts) as ytdl:
        file_path_collector = FilePathCollector()
        ytdl.add_post_processor(file_path_collector)
        result = ytdl.download([link])
        file_path = file_path_collector.file_paths[0]

    return result, file_path

def format_file_name(file_name):
    return re.sub(r"[\\/:*?\"<>|]", "_", file_name)

def get_url_parameter(url, param):
    return parse_qs(urlparse(url).query)[param][0]

def get_video_id_from_metadata(tags):
    links = tags.getall("WOAR")
    if not links or len(links) > 1:
        raise Exception("WOAR tag is in an invalid format")

    return get_url_parameter(str(links[0]), "v")

def get_song_file_path(playlist_name, video_id):
    for file_name in os.listdir(playlist_name):
        file_path = os.path.join(playlist_name, file_name)

        try:
            tags = ID3(song_file_path)
        except:
            # File is not considered a song file if it contains no metadata
            continue

        try:
            if get_video_id_from_metadata(tags) == video_id:
                return file_path
        except:
            continue

    return None

def get_song_file_dict(playlist_name):
    song_file_dict = {}
    duplicate_files = {}
    for file_name in os.listdir(playlist_name):
        song_file_name = file_name
        song_file_path = os.path.join(playlist_name, file_name)

        try:
            tags = ID3(song_file_path)
        except:
            # File is not considered a song file if it contains no metadata
            continue

        try:
            song_video_id = get_video_id_from_metadata(tags)
            song_name = tags.get("TIT2", song_file_name)
            song_track_num = int(str(tags.get("TRCK", 0)))
        except Exception as e:
            print(f"Song file '{file_name}' is in an invalid format and will be ignored")
            continue

        if song_video_id in song_file_dict:
            # Check for duplicate song files
            if song_video_id not in duplicate_files:
                duplicate_files[song_video_id] = [song_file_dict[song_video_id]['file_name']]

            duplicate_files[song_video_id].append(song_file_name)
            continue

        song_file_dict[song_video_id] = {
            "name": song_name,
            "file_name": song_file_name,
            "file_path": song_file_path,
            "track_num": song_track_num
        }

    if duplicate_files:
        exception_strings = []
        for song_video_id, file_names in duplicate_files.items():
            exception_strings.append("\n".join([
                f"The following files link to the same video id '{song_video_id}'",
                "\n".join(["- " + file_name for file_name in file_names])
            ]))

        raise Exception("\n".join([
            "",
            "===========================================================",
            "[ERROR] Duplicate song files found in this playlist folder!",
            "===========================================================",
            "\n\n".join(exception_strings),
            "===========================================================",
            "Please remove duplicate song files to resolve the conflict.",
            "===========================================================",
            ""
        ]))

    return song_file_dict

def setup_config(config: dict):
    new_config = {
        "url": "",
        "reverse_playlist": False,
        "use_title": True,
        "use_uploader": True,
        "use_playlist_name": True,
        "sync_folder_name": True,
        "name_format": "%(title)s-%(id)s.%(ext)s",
        "track_num_in_name": True,
        "audio_format": "bestaudio/best",
        "audio_codec": "mp3",
        "audio_quality": "5",
        "image_format": "jpeg",
        "cookie_file": "",
        "cookies_from_browser": "",
        "verbose": False,
        "include_metadata": {key:True for key in get_metadata_map().keys() if key != "url"}
    }

    for key, value in new_config.items():
        if key in config:
            new_config[key] = config[key]

    return new_config

def generate_default_config(config: dict, config_file_name: str):
    config = setup_config(config)

    # Get list of links in the playlist
    playlist = get_playlist_info(config)

    playlist_name = format_file_name(playlist["title"])

    # Create playlist folder
    Path(playlist_name).mkdir(parents=True, exist_ok=True)

    write_config(os.path.join(playlist_name, config_file_name), config)

def generate_playlist(config: dict, config_file_name: str, update: bool, force_update: bool, regenerate_metadata: bool, single_playlist: bool, current_playlist_name=None):
    # Get list of links in the playlist
    playlist = get_playlist_info(config)
    
    if "entries" not in playlist:
        raise Exception("No videos found in playlist")
    playlist_entries = playlist["entries"]

    if single_playlist:
        playlist_name = "."
    else:
        playlist_name = format_file_name(playlist["title"])

    # Prepare for downloading
    duplicate_name_index = 1
    adjusted_playlist_name = playlist_name
    while True:
        if single_playlist:
            break

        if duplicate_name_index > 1:
            adjusted_playlist_name = f"{playlist_name} ({duplicate_name_index})"

        if update:
            # Check if playlist name changed
            if current_playlist_name is not None and current_playlist_name != adjusted_playlist_name:
                if not config["sync_folder_name"]:
                    adjusted_playlist_name = current_playlist_name
                    break
                try:
                    os.rename(current_playlist_name, adjusted_playlist_name)
                except FileExistsError:
                    duplicate_name_index += 1
                    continue

                print(f"Renaming playlist from '{current_playlist_name}' to '{adjusted_playlist_name}'...")
                if config["use_playlist_name"]:
                    # Regenerate metadata to update album tag with playlist name
                    regenerate_metadata = True
        else:
            # Create playlist folder
            try:
                Path(playlist_name).mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                duplicate_name_index += 1
                continue
        break
    playlist_name = adjusted_playlist_name
            

    write_config(os.path.join(playlist_name, config_file_name), config)
    song_file_dict = get_song_file_dict(playlist_name)
        
    track_num = 1
    skipped_videos = 0
    updated_video_ids = []
    
    # Download each item in the list
    for i, video_info in enumerate(playlist_entries):
        track_num = i + 1 - skipped_videos
        video_id = video_info["id"]
        link = f"https://www.youtube.com/watch?v={video_id}"

        updated_video_ids.append(video_id)
        song_file_info = song_file_dict.get(video_id)
        
        if song_file_info is not None:
            # Skip downloading audio if already downloaded
            print(f"Skipped downloading '{link}' ({track_num}/{len(playlist_entries) - skipped_videos})")

            song_name = song_file_info["name"]
            song_file_name = song_file_info["file_name"]
            song_file_path = song_file_info["file_path"]
            song_track_num = song_file_info["track_num"]

            # Fix name if mismatching
            if config["track_num_in_name"]:
                song_file_name = re.sub(r"^[0-9]+. ", "", song_file_name)
                file_name = f"{track_num}. {song_file_name}"
            else:
                file_name = song_file_name
            file_path = os.path.join(playlist_name, file_name)
            
            # Update song index if not matched
            if song_track_num != track_num and config["include_metadata"]["track"]:
                print(f"Reordering '{song_name}' from position {song_track_num} to {track_num}...")
                update_track_num(song_file_path, track_num)

            if song_file_path != file_path:
                if song_track_num == track_num:
                    # Track number in name was incorrectly modified manually by user
                    print(f"Renaming incorrect file name from '{Path(song_file_path).stem}' to '{Path(file_path).stem}'")
                os.rename(song_file_path, file_path)

            # Generate metadata just in case it is missing
            try:
                force_update_file_name = generate_metadata(file_path, link, track_num, playlist["title"], config, regenerate_metadata, force_update)
                if force_update:
                    force_update_file_path = os.path.join(playlist_name, force_update_file_name)
                    if file_path != force_update_file_path:
                        # Track name needs updating to proper format
                        print(f"Renaming incorrect file name from '{Path(file_path).stem}' to '{Path(force_update_file_path).stem}'")
                    os.rename(file_path, force_update_file_path)
            except Exception as e:
                print(f"Unable to update metadata: {e}")

            # Check if video is unavailable
            if video_info["channel_id"] is None:
                # Video title indicates availability of video such as '[Private Video]'
                print(f"The previous song '{song_name}' is unavailable but a local copy exists - {video_info['title']}")
        else:
            try:
                # Download audio if not downloaded
                print(f"Downloading '{link}'... ({track_num}/{len(playlist_entries) - skipped_videos})")

                # Attempt to download video
                result, file_path = download_song(link, playlist_name, track_num, config)
                
                # Check download failed and video is unavailable
                if result != 0 and video_info["channel_id"] is None:
                    # Video title indicates availability of video such as '[Private Video]'
                    raise Exception(f"Video is unavailable - {video_info['title']}")

                generate_metadata(file_path, link, track_num, playlist["title"], config, False, False)
            except Exception as e:
                print(f"Unable to download video: {e}")
                skipped_videos += 1

    # Move songs that are missing (deleted/privated/etc.) to end of the list
    track_num = len(playlist_entries) - skipped_videos + 1
    for video_id in song_file_dict.keys():
        if video_id not in updated_video_ids:
            song_file_info = song_file_dict[video_id]
            song_name = song_file_info["name"]
            song_file_name = song_file_info["file_name"]
            song_file_path = song_file_info["file_path"]
            song_track_num = song_file_info["track_num"]

            if config["track_num_in_name"]:
                song_file_name = re.sub(r"^[0-9]+. ", "", song_file_name)
                file_name = f"{track_num}. {song_file_name}"
            else:
                file_name = song_file_name
            file_path = os.path.join(playlist_name, file_name)

            if song_track_num != track_num and config["include_metadata"]["track"]:
                print(f"Moving '{song_name}' from position {song_track_num} to {track_num} due to missing video link...")
                update_track_num(song_file_path, track_num)

            if song_file_path != file_path:
                if song_track_num == track_num:
                    # Track number in name was incorrectly modified manually by user
                    print(f"Renaming incorrect file name from '{song_file_name}' to '{file_name}'")
                os.rename(song_file_path, file_path)

            track_num += 1

    print("Download finished.")

def get_existing_playlists(directory: str, config_file_name: str):
    playlists_data = []
    playlists_name_dict = {}
    duplicate_playlists = {}
    for playlist_name in next(os.walk(directory))[1]:
        config_file = os.path.join(directory, playlist_name, config_file_name)
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
            except json.decoder.JSONDecodeError as e:
                print(e)
                print(f"[ERROR] Config file '{config_file}' is in an invalid format. Please fix or remove the config file.")
                continue

            try:
                playlist_id = get_url_parameter(config["url"], "list")
            except:
                print(f"[ERROR] Playlist URL in config file '{config_file}' is in an invalid format. Please fix or remove the config file.")
                continue

            if playlist_id in playlists_name_dict:
                # Check for duplicate playlists
                if playlist_id not in duplicate_playlists:
                    duplicate_playlists[playlist_id] = [playlists_name_dict[playlist_id]]

                duplicate_playlists[playlist_id].append(playlist_name)
                continue

            playlist_data = {
                "playlist_name": playlist_name,
                "config_file": config_file,
                "last_updated": time.strftime('%x %X', time.localtime(os.path.getmtime(config_file)))
            }
            playlists_data.append(playlist_data)
            playlists_name_dict[playlist_id] = playlist_name

    if duplicate_playlists:
        exception_strings = []
        for playlist_id, playlist_names in duplicate_playlists.items():
            exception_strings.append("\n".join([
                "The following playlist folders link to the same playlist id",
                f"Duplicate Playlist ID: '{playlist_id}'",
                "\n".join(["- " + playlist_name for playlist_name in playlist_names])
            ]))

        raise FileExistsError("\n".join([
            "",
            "===========================================================",
            "[ERROR] Duplicate playlist folders found in this directory!",
            "===========================================================",
            "\n\n".join(exception_strings),
            "===========================================================",
            "Please remove duplicate playlists to resolve this conflict.",
            "===========================================================",
            ""
        ]))

    return playlists_data

def get_bool_option_response(prompt, default: bool):
    if default:
        prompt_choice = "Y/n"
    else:
        prompt_choice = "y/N"

    while True:
        response = input(f"{prompt} ({prompt_choice}): ").lower()
        if response == "y" or (default and response == ""):
            return True
        elif response == "n" or (not default and response == ""):
            return False
        else:
            print("Invalid response, please type 'y' or 'n'.")

def get_index_option_response(prompt, count: int):
    if count <= 0:
        raise Exception("Count must be greater than 0")

    index = 0
    while True:
        selected_index = input(f"{prompt} (1 to {count}): ")
        try:
            index = int(selected_index) - 1
            if index >= 0 and index < count:
                break
        except:
            pass
        
        print("Invalid response, please enter a valid number.")

    return index

if __name__ == "__main__":
    print("\n".join([
        "YouTube Music Playlist Downloader v" + version,
        "-----------------------------------------------------------",
        "This program automatically downloads & updates a local copy",
        "of any YouTube playlist in the form of a music album folder",
        "- Songs are stored in album folders named by playlist title",
        "- Existing albums are updated with any new or missing songs",
        "- Songs no longer in the playlist are moved to end of album",
        "- Song metadata is automatically generated using video info",
        "- Metadata includes Title, Artists, Album, and Track Number",
        "- Cover art for songs are created by using video thumbnails",
        "",
        "[NOTE] This program and ffmpeg may be blocked by antivirus.",
        "If you run into any issues, you can try adding this program",
        "and your ffmpeg folder to the exclusions of your antivirus.",
        "-----------------------------------------------------------",
    ]))

    quit_enabled = True
    config_file_name = ".playlist_config.json"

    OPTION_DOWNLOAD = "Download a playlist from YouTube"
    OPTION_UPDATE   = "Update previously saved playlist"
    OPTION_MODIFY   = "Modify previously saved playlist"
    OPTION_GENERATE = "Generate default playlist config"
    OPTION_CHANGE   = "Change current working directory"
    OPTION_EXIT     = "Exit"

    single_playlist = os.path.exists(config_file_name)
    if single_playlist:
        print(f"Current folder detected as a playlist. Running in single playlist mode.\nIf you did not expect this, please remove '{config_file_name}' from this folder.")

    while True:
        try:
            check_ffmpeg()

            config = {}
            playlists_data = {}
            quit_enabled = True
            selected_option = None
            existing_config = None
            update_existing = False
            regenerate_metadata = False
            current_playlist_name = None

            options = [
                OPTION_DOWNLOAD,
                OPTION_GENERATE,
                OPTION_CHANGE,
                OPTION_EXIT
            ]

            if single_playlist:
                # Single playlist in current directory
                while True:
                    try:
                        with open(config_file_name, "r") as f:
                            config = json.load(f)
                        update_existing = True
                        existing_config = config
                        current_playlist_name = os.path.basename(os.getcwd())
                    except (KeyboardInterrupt, EOFError) as e:
                        raise e
                    except json.decoder.JSONDecodeError as e:
                        print(f"\n{e}\n[ERROR] Config file '{config_file_name}' is in an invalid format. Please fix or remove the config file.")
                        quit_enabled = True
                        input("Press 'Enter' to continue after resolving this conflict or close this window to finish.")
                        continue
                    except:
                        print(f"\n[ERROR] Config file '{config_file_name}' could not be found. Please ensure the config file is present.")
                        quit_enabled = True
                        input("Press 'Enter' to continue after resolving this conflict or close this window to finish.")
                        
                    break
            else:
                # Multiple playlists in sub-directories
                while True:
                    try:
                        playlists_data = get_existing_playlists(".", config_file_name)
                    except FileExistsError as e:
                        print(e)
                        quit_enabled = True
                        input("Press 'Enter' to continue after resolving this conflict or close this window to finish.")
                        continue
                    except (KeyboardInterrupt, EOFError) as e:
                        raise e
                    except Exception as e:
                        print(e)
                        print("Failed to get a list of existing playlists")
                    break
                if len(playlists_data) > 0:
                    options.insert(1, OPTION_UPDATE)
                    options.insert(2, OPTION_MODIFY)

                options_formatted = []
                for i, option in enumerate(options):
                    options_formatted.append(f"{i + 1}. {option}")
                print(f"\n" + "\n".join(options_formatted) + "\n")

                selected_option = options[get_index_option_response("Select an option", len(options))]

            quit_enabled = False
            if selected_option == OPTION_DOWNLOAD:
                # Download new playlist
                config = setup_config(config)
                config["url"] = input("Please enter the URL of the playlist you wish to download: ")

                # Check if playlist is already downloaded
                already_downloaded = False
                for playlist_data in playlists_data:
                    try:
                        with open(playlist_data["config_file"], "r") as f:
                            existing_config = json.load(f)

                        if get_url_parameter(existing_config["url"], "list") == get_url_parameter(config["url"], "list"):
                            # Playlist already downloaded
                            already_downloaded = True
                            print("\n" + f"> {playlist_data['playlist_name']} (Last Updated: {playlist_data['last_updated']})" + "\n")
                            update_existing = get_bool_option_response("This playlist is already downloaded. Update playlist?", default=True)
                            if not update_existing:
                                print("Not updating existing playlist.")
                                quit_enabled = True
                                input("Press 'Enter' to return to main menu or close this window to finish.")
                            else:
                                current_playlist_name = playlist_data["playlist_name"]
                            break
                    except KeyboardInterrupt as e:
                        raise e
                    except:
                        continue

                if not already_downloaded and not update_existing:
                    config["reverse_playlist"] = get_bool_option_response("Reverse playlist?", default=False)
                    config["use_title"] = get_bool_option_response("Use title instead of track name?", default=True)
                    config["use_uploader"] = get_bool_option_response("Use uploader instead of artist?", default=True)
                    config["use_playlist_name"] = get_bool_option_response("Use playlist name for album?", default=True)

                    generate_playlist(config, config_file_name, False, False, regenerate_metadata, False, current_playlist_name)
                    quit_enabled = True
                    input("Finished downloading. Press 'Enter' to return to main menu or close this window to finish.")

            if selected_option == OPTION_UPDATE or update_existing:
                # Update existing playlist
                config = None
                if update_existing:
                    config = existing_config
                else:
                    playlists_list = []
                    for i, playlist_data in enumerate(playlists_data):
                        playlists_list.append(f"{i + 1}. {playlist_data['playlist_name']} (Last Updated: {playlist_data['last_updated']})")
                    print("\n" + "\n".join(playlists_list) + "\n")

                    update_index = get_index_option_response("Enter a playlist number to update", len(playlists_data))
                    playlist_data = playlists_data[update_index]

                    current_playlist_name = playlist_data["playlist_name"]
                    with open(playlist_data["config_file"], "r") as f:
                        config = json.load(f)

                # In case settings were somehow missing
                config = setup_config(config)

                print("\n" + "\n".join([
                    f"Updating playlist: {current_playlist_name}",
                    f"URL: {config['url']}",
                ]) + "\n")

                quit_enabled = False
                generate_playlist(config, config_file_name, True, False, False, single_playlist, current_playlist_name)
                quit_enabled = True
                input("Finished updating. Press 'Enter' to return to main menu or close this window to finish.")
            elif selected_option == OPTION_MODIFY:
                # Modify existing playlist
                config = None
                if update_existing:
                    config = existing_config
                else:
                    playlists_list = []
                    for i, playlist_data in enumerate(playlists_data):
                        playlists_list.append(f"{i + 1}. {playlist_data['playlist_name']} (Last Updated: {playlist_data['last_updated']})")
                    print("\n" + "\n".join(playlists_list) + "\n")

                    update_index = get_index_option_response("Enter a playlist number to update", len(playlists_data))
                    playlist_data = playlists_data[update_index]

                    current_playlist_name = playlist_data["playlist_name"]
                    with open(playlist_data["config_file"], "r") as f:
                        config = json.load(f)

                # In case settings were somehow missing
                config = setup_config(config)

                print("\n" + "\n".join([
                    f"Updating playlist: {current_playlist_name}",
                    f"URL: {config['url']}",
                    "",
                    f"Playlist settings",
                    f"- Reverse playlist: {config['reverse_playlist']}",
                    f"- Use title instead of track name: {config['use_title']}",
                    f"- Use uploader instead of artist: {config['use_uploader']}",
                    f"- Use playlist name for album: {config['use_playlist_name']}",
                ]) + "\n")

                if single_playlist:
                    quit_enabled = True

                modify_settings = get_bool_option_response("Change playlist settings?", default=False)
                quit_enabled = False

                if modify_settings:
                    last_use_title = config["use_title"]
                    last_use_uploader = config["use_uploader"]
                    last_use_playlist_name = config["use_playlist_name"]

                    config["reverse_playlist"] = get_bool_option_response("Reverse playlist?", default=False)
                    config["use_title"] = get_bool_option_response("Use title instead of track name?", default=True)
                    config["use_uploader"] = get_bool_option_response("Use uploader instead of artist?", default=True)
                    config["use_playlist_name"] = get_bool_option_response("Use playlist name for album?: ", default=True)

                    # Metadata needs to be regenerated if the settings have been changed
                    if config["use_title"] != last_use_title or config["use_uploader"] != last_use_uploader or config["use_playlist_name"] != last_use_playlist_name:
                        regenerate_metadata = True

                force_update = get_bool_option_response("Force update all names and metadata?", default=False)

                generate_playlist(config, config_file_name, True, force_update, regenerate_metadata, single_playlist, current_playlist_name)
                quit_enabled = True
                input("Finished updating. Press 'Enter' to return to main menu or close this window to finish.")
            elif selected_option == OPTION_GENERATE:
                # Generate default playlist config
                config["url"] = input("Please enter the URL of the playlist to generate config for: ")

                # Check if playlist is already downloaded
                already_downloaded = False
                for playlist_data in playlists_data:
                    try:
                        with open(playlist_data["config_file"], "r") as f:
                            existing_config = json.load(f)

                        if get_url_parameter(existing_config["url"], "list") == get_url_parameter(config["url"], "list"):
                            print(f"Playlist '{playlist_data['playlist_name']}' is already downloaded.")
                            quit_enabled = True
                            input("Press 'Enter' to return to main menu or close this window to finish.")
                            already_downloaded = True
                            break
                    except KeyboardInterrupt as e:
                        raise e
                    except:
                        continue

                if not already_downloaded:
                    generate_default_config(config, config_file_name)
                    quit_enabled = True
                    input("Finished generating default config. Press 'Enter' to return to main menu or close this window to finish.")
            elif selected_option == OPTION_CHANGE:
                # Change current working directory
                target_path = input("Enter path of target playlists folder to change to: ")
                os.chdir(target_path)
            elif selected_option == OPTION_EXIT:
                # Exit
                quit_enabled = True
                raise KeyboardInterrupt
        except (KeyboardInterrupt, EOFError):
            if quit_enabled:
                print("\nQuitting...")
                break

            print("\nCancelling...\n(To exit, select Exit or press Ctrl+C again)")
            continue
        except Exception as e:
            print(e)
            print("Error encountered while generating. Please try again.")
            continue
