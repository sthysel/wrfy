import re
import sys
from fnmatch import fnmatch

import click
from docker import Client

from . import check
from . import util
from .container import Container
from .image import Image


class Config:
    def __init__(self):
        self.verbose = False
        self.force = False


pass_config = click.make_pass_decorator(Config, ensure=True)


# the main group
@click.group()
@click.option('--force', is_flag=True, help='Force action, do not prompt to ask.')
@click.option('--verbose', is_flag=True, help='Verbose logs')
@pass_config
def wrfy(config, verbose, force):
    """Docker tool"""

    config.verbose = verbose
    config.force = force


# image
@wrfy.group('image')
def image():
    """ Docker image common useful operations"""


@image.command('pull')
@pass_config
def pull_all_images(config):
    """pull all images"""

    def status_title(tag, pad=None):
        title = 'pull {}'.format(tag)
        if pad:
            title = '%*s' % (pad, title)
        return title

    def pull_tags(tags):
        pad = max(len(status_title(t)) for t in tags)
        for tag in sorted(tags):
            util.log_action('pulling tag: {}'.format(tag))
            util.print_status_stream(status_title(tag, pad), cli.pull(tag, stream=True))

    cli = Client()
    _tags = Image.repotags(cli)
    if _tags:
        pull_tags(_tags)


@image.command('rm_dangling')
@pass_config
def rmi_dangling(config):
    """remove all dangling (untagged) images"""

    cli = Client()
    to_remove = []
    for image, used_by in check.untagged_images_with_usage(cli):
        if used_by:
            util.log_issue("not removing image: %s (in use by %s)" % (image, used_by))
        else:
            to_remove.append(image)
    if not to_remove:
        return
    background = ['The following dangling images will be removed:\n']
    background += [' - %s\n' % image for image in to_remove]
    if not config.force and not util.confirm_action(
            ''.join(background), 'Remove images?'):
        return
    for image in to_remove:
        util.log_action("removing dangling image: %s" % image)
        util.log_any_error(lambda: cli.remove_image(image.get('Id')))


def match_iterator_glob_or_regexp(args, iterator, apply_fn):
    """
    returns the matching objects from `iterator`, using fnmatch
    unless args.regex is set, in which case regular expression is used.
    `apply_fn` is applied to each object, returning a string to
    check match against.
    """
    if args.regex:
        print('Using regular expression')
        r = re.compile(args.pattern)

        def matcher(s):
            return r.match(s)
    else:
        def matcher(s):
            return fnmatch(s, args.pattern)

    for obj in iterator:
        match = apply_fn(obj)
        if matcher(match):
            yield obj


@image.command('rm_matching')
@click.argument('--tag', help='Tag pattern')
@click.argument('--regex', is_flag=True, help='Use regular expression in stead of glob')
@pass_config
def rmi_matching(config, tag, regex):
    """remove images which have tags matching `pattern`"""

    cli = Client()

    def all_image_tags():
        for image in Image.all(cli):
            for tag in image.tags:
                yield tag

    to_remove = list(match_iterator_glob_or_regexp(config, all_image_tags(), lambda t: t))
    if not to_remove:
        return
    background = ['Images with the following tags will be deleted:\n']
    for tag in sorted(to_remove):
        background.append(' - %s\n' % tag)
    if not config.force and not util.confirm_action(''.join(background), 'Delete matching images?'):
        return
    for tag in to_remove:
        util.log_action("removing image via tag: %s" % tag)
        util.log_any_error(lambda: cli.remove_image(tag))


# container
@wrfy.group()
def container():
    """Container"""


@container.command('killall')
@pass_config
def kill_all(config):
    """kill all running containers"""

    cli = Client()
    to_kill = list(sorted(Container.all(cli), key=repr))
    if not to_kill:
        return
    background = ['The following running containers will be killed:\n']
    background += [' - %s\n' % container for container in to_kill]
    if not config.force and not util.confirm_action(''.join(background), 'Kill containers?'):
        return

    for container in to_kill:
        util.log_action("killing container: %s" % container)
        util.log_any_error(lambda: cli.kill(container.get('Id')))


@click.command()
@pass_config
def rm_stopped(config):
    """remove all containers which are not running"""

    cli = Client()
    to_remove = list(check.stopped_containers(cli))
    if not to_remove:
        return
    background = ['The following stopped containers will be removed:\n']
    background += [' - %s\n' % container for container in to_remove]
    if not config.force and not util.confirm_action(
            ''.join(background), 'Remove containers?'):
        return
    for container in to_remove:
        util.log_action("removing container: %s" % (container))
        util.log_any_error(lambda: cli.remove_container(container.get('Id')))


# Volumes
@click.command()
def rmv_dangling(args):
    """remove all dangling volumes"""

    cli = Client()
    to_remove = list(check.dangling_volumes(cli))
    if not to_remove:
        return
    background = ['The following dangling volumes will be removed:\n']
    background += [' - %s\n' % volume for volume in to_remove]
    if not args.force and not util.confirm_action(
            ''.join(background), 'Remove volumes?'):
        return
    for volume in to_remove:
        util.log_action("removing dangling volume: %s" % (volume))
        cli.remove_volume(volume.name)


@click.command()
@pass_config
def scrub(config):
    """remove all stopped containers, dangling images and volumes"""

    rm_stopped(config)
    rmi_dangling(config)
    rmv_dangling(config)


@click.command()
@pass_config
def rm_matching(config):
    """remove containers whose name matches `pattern`"""

    cli = Client()
    to_remove = list(match_iterator_glob_or_regexp(config.force, check.stopped_containers(cli), lambda c: c.name))
    if not to_remove:
        return
    background = ['The following containers will be deleted:\n']
    for container in sorted(to_remove, key=repr):
        background.append(' - %s\n' % container)
    if not config.force and not util.confirm_action(''.join(background), 'Delete matching containers?'):
        return
    for container in to_remove:
        util.log_action("removing container via tag: %s" % container)
        util.log_any_error(lambda: cli.remove_container(container.get('Id')))


@wrfy.command('doctor')
@pass_config
def doctor(config):
    """ check for common issues """

    cli = Client()
    util.log_issues("containers running from old version of tag", "restart containers", check.check_latest_image(cli))
    util.log_issues("dangling volumes", "wrfy rmv-dangling", check.check_dangling_volumes(cli))
    util.log_issues("dangling images", "wrfy rmi-dangling", check.check_untagged_images(cli))
    util.log_warnings("stopped containers", "wrfy rm-stopped", check.check_stopped_containers(cli))


def version():
    import pkg_resources
    version = pkg_resources.require("wrfy")[0].version
    print('''\
wrfy, version %s

Copyright © 2016 Grahame Bowland
License GPLv3+: GNU GPL version 3 or later <http://gnu.org/licenses/gpl.html>.
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.''' % (version))
    sys.exit(0)
