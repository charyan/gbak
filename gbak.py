'''
Copyright (c) 2024 Yannis Charalambidis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import argparse
import requests
import datetime
from halo import Halo

import logging
import os
import time

import signal

# Set up logging
logging.basicConfig(level=logging.INFO)

SIGNINT = False
RATE_LIMIT_WAIT_TIME_S = 10
RATE_LIMIT_WARNING_THRESHOLD = 10


def signal_handler(_, __):
    """
    Signal handler function that handles the SIGINT signal.

    Args:
        _ (int): The signal number.
        __ (frame): The current stack frame.

    Returns:
        None
    """
    global SIGNINT
    SIGNINT = True


def sizeof_fmt(num, suffix="B"):
    """
    Convert a number representing a file size to a human-readable format.

    Args:
        num (float): The number representing the file size.
        suffix (str, optional): The suffix to append to the formatted size. Defaults to "B".

    Returns:
        str: The human-readable formatted size.

    References:
        This function is based on the Stack Overflow answer by Fred Cirera.
        Link: https://stackoverflow.com/a/1094933
    """
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def rate_limit_check(r):
    """
    Checks the rate limit remaining in the response headers and waits if approaching the rate limit threshold.

    Args:
        r (Response): The HTTP response object.

    Returns:
        None
    """

    if 'X-RateLimit-Remaining' in r.headers and int(r.headers['X-RateLimit-Remaining']) < RATE_LIMIT_WARNING_THRESHOLD:
        logging.warning('Approaching rate limit')
        wait_time_s = RATE_LIMIT_WAIT_TIME_S
        spinner = Halo(
            spinner='dots', text=f'Approaching rate limit. Waiting {wait_time_s} seconds.', color='yellow')
        spinner.start()

        for i in range(0, wait_time_s):
            spinner.text = f'Approaching rate limit. Waiting {wait_time_s - i} seconds.'
            if SIGNINT:
                spinner.stop()
                exit(0)
            time.sleep(1)

        spinner.stop()


def make_request(url, headers):
    """
    Makes a request to the GitHub API.

    Args:
        url (str): The URL to make the request to.
        headers (dict): The headers to include in the request.

    Returns:
        Response: The HTTP response object.

    Raises:
        SystemExit: If there is a connection error, timeout, or too many redirects.
    """
    try:
        r = requests.get(url, headers=headers)
        rate_limit_check(r)
        r.raise_for_status()

    except requests.exceptions.HTTPError as err:
        logging.error(f'Failed to connect to GitHub: {err.response.text}')
        exit(-1)
    except requests.exceptions.ConnectionError:
        logging.error('Failed to connect to GitHub')
        exit(-1)
    except requests.exceptions.Timeout:
        logging.error('Request timed out')
        exit(-1)
    except requests.exceptions.TooManyRedirects:
        logging.error('Too many redirects')
        exit(-1)

    return r


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description='Backup the given user\'s GitHub repositories.',
                                     prog='gbak', usage='%(prog)s [options] user dest', formatter_class=argparse.RawTextHelpFormatter,
                                     epilog="""
Example: gbak myuser ~/backup/

Set the environment variable GITHUB_PERSONAL_ACCESS_TOKEN for authentication.
Authentication is required to access private repositories and to avoid rate limiting.
See https://docs.github.com/en/github/authenticating-to-github/creating-a-personal-access-token for more information.

This software is licensed under the MIT License.
""")
    parser.add_argument('user', help='GitHub user')
    parser.add_argument('dest', help='Destination directory')
    parser.add_argument('-v', '--version', action='version',
                        version='%(prog)s 0.1.0')
    parser.add_argument('--all-branches', '-a',  action='store_true', dest='all_branches',
                        help='Backup all branches (default: only default branch)')
    parser.add_argument('--tar-gz', '-t', action='store_true', dest='tar_gz',
                        help='Compress the backup files using tar and gzip (default: zip)')
    args = parser.parse_args()

    user = args.user
    all_branches = args.all_branches
    tar_gz = args.tar_gz

    file_extension = 'zip'
    if tar_gz:
        file_extension = 'tar.gz'

    dest = args.dest
    if not dest.endswith('/'):
        dest += '/'

    if not os.path.isdir(args.dest):
        logging.error('Invalid destination directory')
        exit(-1)

    headers = {}

    authenticated = False

    if 'GITHUB_PERSONAL_ACCESS_TOKEN' in os.environ:
        authenticated = True
        headers['Authorization'] = f'token {os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]}'

    repos = make_request(
        f'https://api.github.com/search/repositories?q=user:{user}', headers).json()['items']

    timestamp = datetime.datetime.now().isoformat()
    spinner = Halo(spinner='dots')
    bytes_total = 0

    try:
        _ = repos[0]['name']
    except KeyError:
        logging.error(f'Invalid response from GitHub: {repos}')
        exit(-1)

    logging.info(
        f"Starting backup of {user}\'s {'public ' if not authenticated else ''}GitHub repositories at {timestamp}")

    try:
        os.makedirs(f"{dest}{timestamp}", exist_ok=False)
    except OSError:
        logging.error(
            f'Failed to create directory {dest}/{timestamp}')
        exit(-1)

    for repo in repos:
        def log_fmt(
            message, name, branch): return f'{message:8s}  {name:20s} {("[" + branch+ "]"):20s}'

        repo_name = repo['name']
        branches = [repo['default_branch']]

        if all_branches:
            branches = [branch['name'] for branch in make_request(
                f'https://api.github.com/repos/{user}/{repo_name}/branches', headers).json()]

        for branch in branches:
            if SIGNINT:
                break

            spinner.text = log_fmt('Fetching', repo_name, branch)
            spinner.start()

            r = make_request(
                f'https://github.com/{user}/{repo_name}/archive/refs/heads/{branch}.{file_extension}', headers=headers)

            with open(f'{dest}{timestamp}/{repo_name}_{branch}.{file_extension}', 'wb') as f:
                bytes_written = f.write(r.content)
                spinner.succeed(
                    f'{log_fmt("Saved", repo_name, branch)} ({sizeof_fmt(bytes_written)})'
                )
                bytes_total += bytes_written

    logging.info(
        f'Backup completed at {datetime.datetime.now().isoformat()}, {sizeof_fmt(bytes_total)} written to {dest} in {(datetime.datetime.now() - datetime.datetime.fromisoformat(timestamp)).total_seconds() / 60:.1f} minutes',
    )
    spinner.stop()
