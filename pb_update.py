#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright © 2014 Kevin Thibedeau
# (kevin 'period' thibedeau 'at' gmail 'punto' com)
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

'''Picoblaze ROM update script
'''
from __future__ import print_function, division

import sys
import os
import re
import math
import copy
import itertools
from optparse import OptionParser
from subprocess import check_call, CalledProcessError

try:
  from opbasm_lib.color import *
except ImportError:
  # Provide dummy functions if the color module isn't found
  def note(t): return t
  def success(t): return t
  def warn(t): return t
  def error(t): return t


def parse_command_line():
    progname = os.path.basename(sys.argv[0])
    usage = '{} -m <mem file> -n <NCD file> [-r <RAM inst name>] [-o <output bit file>]'.format(progname)
    parser = OptionParser(usage=usage)

    parser.add_option('-m', '--mem', dest='mem_file', help='mem file')
    parser.add_option('-n', '--ncd', dest='ncd_file', help='NCD file')
    parser.add_option('-r', '--ram_inst', dest='ram_inst', help='RAM instance name')
    parser.add_option('-o', '--output', dest='out_bit_file', help='Output bit file')

    options, args = parser.parse_args()

    if not options.mem_file: parser.error('Missing mem file')
    if not options.ncd_file: parser.error('Missing NCD file')

    return options

def report_error(*args, **kwargs):
  print(error('ERROR:'), *args, file=sys.stderr)
  if 'exit' in kwargs:
    sys.exit(kwargs['exit'])


def main():
    print(note('Picoblaze ROM updater'))
    options = parse_command_line()

    # Set up file names
    design_name = os.path.splitext(os.path.basename(options.ncd_file))[0]
    prog_name = os.path.splitext(os.path.basename(options.mem_file))[0]
    bit_file = os.path.splitext(options.ncd_file)[0] + '.bit'
    xdl_file = design_name + '.xdl'
    bmm_file = prog_name + '.bmm'

    if not options.out_bit_file:
        options.out_bit_file = 'new_' + design_name + '.bit'

    # Check for existence of input files
    if not os.path.exists(options.mem_file):
        report_error('mem file not found', exit=1)

    if not os.path.exists(options.ncd_file):
        report_error('NCD file not found', exit=1)

    if not os.path.exists(bit_file):
        report_error('bit file not found ({})'.format(bit_file), exit=1)

    # Check timestamp of XDL and NCD to see if we need to update the XDL
    run_xdl = True
    if os.path.exists(xdl_file):
        if os.path.getmtime(xdl_file) > os.path.getmtime(options.ncd_file):
            run_xdl = False

    # Run XDL to get instance information
    if run_xdl:
        print('Running XDL...')
        try:
          check_call(['xdl', '-ncd2xdl', options.ncd_file, xdl_file])
        except CalledProcessError:
          report_error('XDL failure', exit=1)

    if not os.path.exists(xdl_file):
        report_error('XDL file not generated', exit=1)

    # Find BRAM instances in XDL file
    ram_insts = find_ram_instances(xdl_file)
    inames = sorted(ram_insts.keys())

    if len(inames) == 0:
        report_error('No RAM instances found', exit=1)

    prompt_user = True

    lo = MemLayout()
    
    if len(inames) == 1: # Don't bother with prompt if only one RAM is in the netlist
      lo.add_row(MemRow(0,0))
      lo.rows[0].add_bram(copy.deepcopy(ram_insts[inames[0]]))
      prompt_user = False
    elif options.ram_inst is not None: # Check if named instance(s) exists
      spec = [r.split(',') for r in options.ram_inst.split(':')]
      spec_flat = [i for s in spec for i in s]

      def show_instances(inames, ram_insts):
        print(' Available RAM instances:', file=sys.stderr)
        for n in inames:
          print('  {}  [{} {}]'.format(n, ram_insts[n].primitive, ram_insts[n].dimensions), file=sys.stderr)
        sys.exit(1)

      # Check for duplicate instances in the spec
      if len(set(spec_flat)) < len(spec_flat):
        report_error('Duplicate instances in BRAM spec')
        show_instances(inames, ram_insts)

      # Verify all instances in the spec are in the netlist
      for i in spec_flat:
        if i not in inames:
          report_error('Instance does not exist in netlist ({})'.format(i))
          show_instances(inames, ram_insts)

      # Build a layout from the specification
      for r in spec:
        lo.add_row(MemRow(0,0))
        for b in r:
          lo.rows[-1].add_bram(copy.deepcopy(ram_insts[b]))

      prompt_user = False


    # Read the mem file
    mem_data = []
    with open(options.mem_file, 'r') as fh:
      mem_data = [l.strip() for l in fh.readlines()]

    if len(mem_data) > 1: # Remove first line with address offset
      mem_data = mem_data[1:]

    print('Required memory depth:', len(mem_data))

    # Interactive selection of BRAM blocks to build memory layout
    target_width = 18
    target_depth = len(mem_data)
    if prompt_user: lo.add_row(MemRow(0, 0)) # Start with a default, empty row

    while prompt_user and not lo.valid(target_depth, target_width):
      if len(lo.rows[0].brams) > 0:
        # Show current layout
        print('\nMemory layout:')
        print('\n'.join(lo.summary(4)))

      if len(inames) == 0:
        report_error('No BRAMs remaining', exit=1)

      # Prompt user
      print('\nAvailable RAM instances:')
      for i, n in enumerate(inames):
        print('  {}) {}  [{} {}]'.format(i+1, n, ram_insts[n].primitive, ram_insts[n].dimensions))
      print('  q) Quit')
      sel = raw_input('\nSelect RAM instance: ')
      if sel.lower() == 'q': sys.exit(0)

      try:
        sel = int(sel) - 1
      except ValueError:
        sel = -1

      if sel < 0 or sel >= len(inames):
        report_error('Invalid selection', exit=1)

      lo.rows[-1].add_bram(copy.deepcopy(ram_insts[inames[sel]]))
      if lo.rows[-1].width > target_width:
        report_error('Row is too wide ({} bits)'.format(lo.rows[-1].width), exit=1)

      if lo.rows[-1].valid(target_width) and not lo.valid(target_depth, target_width):
        # Row is complete but depth is not satisfied
        lo.add_row(MemRow(0, 0))

      # Remove selected BRAM from available list
      inames.remove(inames[sel])



    # Show final layout
    print('\nFinal memory layout:')
    print('\n'.join(lo.summary(4)))
    print()

    # Make sure all BRAMs in layout have the same width
    widths = [b.width for r in lo.rows for b in r.brams]
    if len(widths) > 0:
      if not all(widths[0] == w for w in widths[1:]):
        report_error('Mixed width BRAMs not supported in memory layout', exit=1)

    # Make sure the layout depth is correct
    if lo.rows[-1].end + 1 != target_depth:
      report_error('Memory layout depth does not match required {} words'.format(target_depth), exit=1)

    print('Instances: "{}"'.format(lo.instance_spec))

    # Generate BMM file
    with open(bmm_file, 'w') as fh:
        fh.write(lo.bmm)


    # Run data2mem
    print('\nRunning data2mem...')
    d2m_cmd = ['data2mem', '-bm', bmm_file, '-bd', options.mem_file, \
               '-bt', bit_file, '-bx', '.','-o', 'b', options.out_bit_file]
    print(' ', ' '.join(d2m_cmd))
    try:
      check_call(d2m_cmd)
    except CalledProcessError:
      report_error('data2mem failure', exit=1)


    print(success('Generated updated bit file:'), options.out_bit_file)
    sys.exit(0)


class Bram(object):
  '''Keep track of properties of BRAM instances'''
  def __init__(self, instance, primitive, loc, depth=None, width=None):
    self.instance = instance
    self.primitive = primitive
    self.loc = loc
    self.depth = depth
    self.width = width
    self.lsb = 0

  @property
  def msb(self):
    return self.lsb + self.width - 1

  @property
  def dimensions(self):
    return '{}x{}'.format(self.depth if self.depth else '??', self.width if self.width else '??')


inst_re = re.compile(r'inst "([^"]+)" "(RAMB\d+.*)",.+RAMB\d+_(\w+) *,')

# The XDL file has different BRAM attributes depending on the device family
# We need to extract the depth and width of the memories in different ways
cfg_s3_re = re.compile(r'PORTA_ATTR::(\d+)X(\d+)')
cfg_s6_re = re.compile(r'DATA_WIDTH_A::(\d+)')
cfg_v7_re = re.compile(r'READ_WIDTH_A::(\d+)')

def find_ram_instances(xdl_file):
    with open(xdl_file, 'r') as fh:
        lines = fh.readlines()

    ram_insts = {}
    last_bram = None
    for l in lines:
      if last_bram is not None:
        primitive = ram_insts[last_bram].primitive

        # Spartan-3
        m = cfg_s3_re.search(l)
        if m:
          ram_insts[last_bram].depth = m.group(1)
          ram_insts[last_bram].width = m.group(2)
          last_bram = None

        # Spartan-6
        m = cfg_s6_re.search(l)
        if m:
          width = int(m.group(1))
          width_np = 2**int(math.log(width, 2)) # Remove any parity bits from width
          if '16' in primitive:
            depth = 16384 // width_np
          elif '8' in primitive:
            depth = 8192 // width_np
          else:
            depth = None

          ram_insts[last_bram].depth = depth
          ram_insts[last_bram].width = width
          last_bram = None

        # 7-series
        m = cfg_v7_re.search(l)
        if m:
          width = int(m.group(1))
          width_np = 2**int(math.log(width, 2)) # Remove any parity bits from width
          if '36' in primitive:
            depth = 32768 // width_np
          elif '18' in primitive:
            depth = 16384 // width_np
          else:
            depth = None

          ram_insts[last_bram].depth = depth
          ram_insts[last_bram].width = width
          last_bram = None
          
      m = inst_re.match(l)
      if m:
          ram_insts[m.group(1)] = Bram(m.group(1), m.group(2), m.group(3))
          last_bram = m.group(1)

    return ram_insts


class MemRow(object):
  def __init__(self, start, end, brams=None):
    self.start = start
    self.end = end
    self.brams = brams if brams else []
    self.target_width = 18

    lsb = 0
    for b in reversed(self.brams):
      b.lsb = lsb
      lsb += b.width

    if self.target_width > 0 and len(self.brams) > 0:
      lsb_shift = self.target_width - self.brams[0].width
      for b in self.brams:
        b.lsb += lsb_shift

  @property
  def depth(self):
    return self.end - self.start + 1

  @property
  def width(self):
    return sum(b.width for b in self.brams)

  def add_bram(self, b):
    self.brams.append(b)
    if len(self.brams) >= 2:
      b.lsb = self.brams[-2].lsb - b.width
    else: # First BRAM in row
      b.lsb = self.target_width - b.width
      self.end = self.start + b.depth - 1

  def valid(self, width):
    return True if self.width == width else False

  # FIXME: Remove OUTPUT parameter from bit lane def.
  @property
  def bus_block(self):
    lanes = ['    {} [{}:{}] LOC = {} OUTPUT = {}.mem;'.format(b.instance, b.msb, b.lsb, b.loc, b.loc) for b in self.brams]
    return '  BUS_BLOCK\n' + '\n'.join(lanes) + '\n  END_BUS_BLOCK;'

  def summary(self, indent=0):
    return ['{}{:20}  {:7}  {:2} - {:<2}'.format(' '*indent, b.instance, b.dimensions, \
            b.lsb + b.width-1, b.lsb) for b in self.brams]

  def map(self):
    m = ''.join('[{}]'.format(b.width) for b in self.brams)
    if self.width == self.target_width:
      m = success(m)
    else:
      m = m + error('(?)')

    return m


class MemLayout(object):
  def __init__(self):
    self.rows = []

  @property
  def bmm(self):
    first_bram = self.rows[0].brams[0]
    size = first_bram.depth * first_bram.width
    btype = 'unknown'
    if size <= 1024*16:
      btype = 'RAMB16'
    elif size <= 1024*18:
      btype = 'RAMB18'
    elif size <= 1024*32:
      btype = 'RAMB32'
    elif size <= 1024*36:
      btype = 'RAMB36'


    blocks = [r.bus_block for r in self.rows]
    # data2mem has some goofy way of counting "words" that changes depending on how
    # many BRAMs are in a row
    end_addr = (self.rows[-1].end+1)*len(self.rows[0].brams)-1
    return 'ADDRESS_SPACE pb_rom {} INDEX_ADDRESSING [0x{:08X}:0x{:08X}]\n'.format(btype, 0, end_addr) + \
            '\n'.join(blocks) + '\nEND_ADDRESS_SPACE;'

  @property
  def instance_spec(self):
    return ':'.join(','.join(b.instance for b in r.brams) for r in self.rows)

  def add_row(self, row, relative_start=True):
    if relative_start and len(self.rows) > 0:
      row.start = row.start + self.rows[-1].end + 1
      row.end = row.end + self.rows[-1].end + 1
    self.rows.append(row)

  def valid(self, depth, width):
    rv = all(r.valid(width) for r in self.rows)
    return True if rv and sum(r.depth for r in self.rows) == depth else False

  def summary(self, indent=0):
    for i, r in enumerate(self.rows):
      yield '{}Row {}:  {:4} - {:<4}    {}'.format(' '*indent, i, r.start, r.end, r.map())
      for s in r.summary(indent + 2):
        yield s

if __name__ == '__main__':
    main()
