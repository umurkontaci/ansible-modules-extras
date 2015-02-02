#!/usr/bin/python
from subprocess import Popen
import os

DOCUMENTATION = '''
---
# If a key doesn't apply to your module (ex: choices, default, or
# aliases) you can use the word 'null', or an empty list, [], where
# appropriate.
module: swap
short_description: Add and remove swap
description:
    - Add and remove swaps
    - Resize swaps
    - Make them permanent / temporary through fstab
version_added: "0.1"
author: Umur Kontaci
options:
# One or more of the following
    option_name: state
        description:
            - > I(present) creates the swap file,
            formats it and mounts it as a swap file
            - I(absent) unmounts the swap file, and deletes it
        required: true
        default: present
        choices: ['present', 'absent']
        version_added: "0.1"

    option_name: path
        description:
            - Path of the swap file
        required: true
        version_added: "0.1"

   option_name: size
        description:
            - Size of the swap file in MB
            - Required when C(state) is I(present)
        required: false
        version_added: "0.1"

   option_name: persistent
        description:
            - > When C(persistent) is I(True),
            M(swap) creates an entry in C(/etc/fstab)
            - > When C(persistent) is I(False),
            there is no entry in C(/etc/fstab)
            - > If C(persistent) is I(False), the swap file will stay,
             but it won't be mounted on the next reboot.
            - Required when C(state) is I(present)
        required: false
        version_added: "0.1"


'''

EXAMPLES = '''
- swap: state=present path=/myswap size=512 persistent=no
- swap: state=absent path=/myswap
'''


class Swap(object):
    block_size = 1024 * 1024  # MBs

    def __init__(self, module):
        self.module = module
        if self.module.params['state'] == 'present':
            self.present = True
        else:
            self.present = False

        self.state = self.module.params['state']
        self.path = self.module.params['path']
        self.persistent = bool(self.module.params['persistent'])
        if self.present:
            if 'size' in self.module.params:
                self.size = int(self.module.params['size'])
            else:
                self.size = -1
                raise Exception('Size is required by state is present')

        self.changes = []

        self.actual_present = self.check_actual_present()
        self.actual_size = self.check_actual_size()
        self.actual_persistent = self.check_actual_persistent()

    def check_actual_present(self):
        cmd = ['sh', '-c', 'swapon -s | tail -n +2 | awk \'{print $1}\'']
        rc, out, err = self.module.run_command(cmd, check_rc=True)
        paths = out.split('\n')
        return self.path in paths

    def check_actual_size(self):
        if os.path.isfile(self.path):
            return os.path.getsize(self.path) / self.block_size
        else:
            return -1

    def check_actual_persistent(self):
        with open('/etc/fstab') as fstab:
            lines = fstab.readlines()

        for line in lines:
            if line.startswith(self.path):
                return True
        return False

    def _get_fallocate_command(self, path, size):
        return ['fallocate', '-l', '%d' % (size * self.block_size), path]

    def _get_dd_command(self, path, size):
        return ['dd', 'if=/dev/zero', 'of=%s' % path, 'bs=%s' % self.block_size, 'count=%s' % size]

    def create_swap(self, path, size):
        self.changes.append('create_swap')
        cmd_create_fallback = None
        if self.module.check_mode:
            return
        cmd_check_fallocate = ['which', 'fallocate']
        rc, out, err = self.module.run_command(cmd_check_fallocate)

        if rc == 0:
            self.changes.append('using fallocate')
            cmd_create = self._get_fallocate_command(path, size)
            cmd_create_fallback = self._get_dd_command(path, size)
        else:
            self.changes.append('using dd')
            cmd_create = self._get_dd_command(path, size)

        rc, out, err = self.module.run_command(cmd_create)
        if rc != 0:
            if cmd_create_fallback is not None:
                rc, out, err = self.module.run_command(cmd_create_fallback)
                if rc != 0:
                    raise Exception('Create swap failed')

        cmd_mkswap = ['mkswap', path]
        rc, out, err = self.module.run_command(cmd_mkswap)
        if rc != 0:
            raise Exception('mkswap failed')

        cmd_swapon = ['swapon', path]
        rc, out, err = self.module.run_command(cmd_swapon)
        if rc != 0:
            raise Exception('swapon failed')

    def remove_swap(self, path):
        self.changes.append('remove_swap')
        if self.module.check_mode:
            return
        cmd_swapoff = ['swapoff', path]
        rc, out, err = self.module.run_command(cmd_swapoff)
        if rc != 0:
            raise Exception('swapoff failed')

        cmd_rmswap = ['rm', path]
        rc, out, err = self.module.run_command(cmd_rmswap)
        if rc != 0:
            raise Exception('remove swap file failed')

    def resize_swap(self, path, new_size):
        self.changes.append('resize_swap')
        if self.module.check_mode:
            return
        self.remove_swap(path)
        self.create_swap(path, new_size)

    def make_persistent(self, path):
        self.changes.append('make_persistent')
        if self.module.check_mode:
            return
        with open('/etc/fstab', 'a') as fstab:
            fstab.write('%s\tnone\tswap\tsw\t0\t0\n' % path)

    def make_temporary(self, path):
        self.changes.append('make_temporary')
        if self.module.check_mode:
            return
        with open('/etc/fstab') as fstab:
            lines = fstab.readlines()

        new_lines = []
        for line in lines:
            if not path in line:
                new_lines.append(line)

        with open('/etc/fstab', 'w') as fstab:
                fstab.writelines(new_lines)

    def run(self):
        self.changes.append('actual present: %s' % self.actual_present)
        self.changes.append('actual size: %s' % self.actual_size)
        self.changes.append('actual persistence: %s' % self.actual_persistent)

        self.changes.append('req present: %s' % self.present)
        if self.present:
            self.changes.append('req size: %s' % self.size)
            self.changes.append('req persistence: %s' % self.persistent)

        changed = False
        # we need it
        if self.present:
            # when actual is not present
            if not self.actual_present:
                changed = True
                self.create_swap(self.path, self.size)
                if self.persistent and not self.actual_persistent:
                    self.make_persistent(self.path)
            else:
                # when actual is present but has different size
                if self.actual_size != self.size:
                    print '%d %d' % (self.actual_size, self.size)
                    changed = True
                    self.resize_swap(self.path, self.size)
                # when the requested persistence doesn't match
                if self.actual_persistent != self.persistent:
                    changed = True
                    if self.persistent:
                        self.make_persistent(self.path)
                    else:
                        self.make_temporary(self.path)
        # we don't need it
        else:
            # we don't need it but we have it
            if self.actual_present:
                changed = True
                if self.actual_persistent:
                    self.make_temporary(self.path)
                self.remove_swap(self.path)
        return changed, self.changes


def main():
    module = AnsibleModule(
        argument_spec={
            'state':  {
                'default': 'present',
                'choices': ['present', 'absent']
            },
            'path': {
                'required': True
            },
            'size': {
                'required': False,
                'type': 'int'
            },
            'persistent': {
                'default': False,
                'type': 'bool',
                'choices': BOOLEANS
            },
        },
        supports_check_mode=True,
    )
    swap = Swap(module)
    try:
        changed, changes = swap.run()
        module.exit_json(changed=changed, changes=changes)
    except Exception as e:
        module.fail_json(msg=[x for x in e])

from ansible.module_utils.basic import *
main()
