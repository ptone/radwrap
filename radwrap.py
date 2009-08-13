#!/usr/bin/env python -u
# encoding: utf-8
"""
radwrap.py

Created by Preston Holmes on 2009-03-10.
Copyright (c) 2009 __MyCompanyName__. All rights reserved.

todo:
test for presence of otool first
need radmind config file - or determine where to read defaults
need a cleanup routine
need to check exit code of radmind tools at various points
logging
need to have bless work for any target? Need an option?
do I have the radmind options for covered?
"""

import sys
import os
import tempfile
import logging
# import getopt
from optparse import OptionParser,OptionGroup
from subprocess import Popen, PIPE,call
from glob import glob
import re
import shutil



os.environ['PATH'] = '/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin'

class Usage(Exception):
    def __init__(self, msg):
        self.msg = msg

class RadmindError(Exception):
    def __init__(self, msg):
        self.msg = msg
        
def sh(cmd):
    return Popen(cmd,shell=True,stdout=PIPE,stderr=PIPE).communicate()[0]

def uniq(the_list):
    d = {}
    for x in the_list: 
        if x:
            d[x]=x
    return d.values()

    
def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        # logging.basicConfig(filename=log_filename,level=logging.DEBUG,)

        #settings
        log_filename = '/var/log/radmindWrapper.log'
        temp_dir = os.path.join(tempfile.gettempdir(),'radwrap')
        tools_dir = os.path.join(temp_dir,'tools')
        applicable_transcript = os.path.join(temp_dir,'applicable.T')
        dyld_path = '/usr/lib/dyld'
        dyld_being_replaced = False

        # User replaceable settings:
        # ,'/usr/sbin/bless' - doesn't work from inside chroot
        tools_needed = ['/bin/sh', '/sbin/reboot','/sbin/halt'] 
        pre_update_dirs = ('/usr/local/bin', '/Library/Management')
        radmind_server = 'radmind.sanroque.net'
        ihook_image_directory = '/Network/Library/management08/radmind/images/'
        radmind_port = 6222
        radmind_comparison_path = '.'
        ktcheck_flags = [   '-C', 
                            '-c', 'sha1', 
                            '-h', radmind_server, 
                            '-p', str(radmind_port)]
                            
        fsdiff_flags = [    '-A',
                            '-I', 
                            '-o', applicable_transcript, 
                            '-%' ]
                            
        lapply_flags = [    '-I',
                            '-F',
                            '-C', 
                            '-i', 
                            '-%', 
                            '-h',radmind_server, 
                            '-p',str(radmind_port), 
                            applicable_transcript]
                            
        
        parser = OptionParser()
        parser.add_option ("-v", "--verbose", action="store_true",
                          help="display verbose output",default=False)
        parser.add_option ('-i','--ihook', action="store_true", default=False, dest = "use_ihook",
                                    help = "using iHook for output display")             
        # todo: use logging module
        # parser.add_option ("-o", "--output", dest="output", 
        parser.add_option ("-n", "--no-reboot", action = "store_false", default = True, dest = "reboot_after",
                            help = "if no-system files are modified - skip reboot")
        parser.add_option ('-K','--command-file', help = "specify command file to use", metavar = "PATH OR NAME")
        # parser.add_option ("-c","--config"
        
        # todo:
        # parser.usage =
        (options, args) = parser.parse_args()
        
        default_command = 'default.K'


        if os.geteuid() != 0:
            raise Usage ('must be run as root')
        
        # todo: check for .noradmind file in /Library/Management
        
        if len(args) != 1:
            if len(args) > 1:
                parser.error ('too many arguments, only target path is required argument')
            elif len(args) < 1:
                parser.error ('no target path supplied')
        elif not os.path.exists(args[0]):
            parser.error ('target path does not exist')
        else:
            radmind_root = args[0]
        

        try:
            shutil.rmtree(temp_dir)
        except:
            pass
            
        try: 
            os.makedirs(tools_dir)
        except:
            # todo:  test whether error is because already exists
            raise
        
        os.chdir(radmind_root)
        #disable sleep
        sh('pmset sleep 0')
        # todo:  disable spotlight and time machine during lapply
        
        if options.use_ihook: 
            print '%BECOMEKEY'
            print '%UIMODE AUTOCRATIC'    
            print '%WINDOWLEVEL HIGH'
            print '%BEGINPOLE'
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'ktcheck.tif')
            ktcheck_flags.append('-q')
        print 'Checking for changes from server'
        # print ktcheck_flags
        return_value = call(['ktcheck'] + ktcheck_flags)
        # return_value = call(['ktcheck'] + ktcheck_flags,shell=True)
        if return_value > 1:
            raise RadmindError, 'ktcheck returned %s' % return_value
        if return_value == 1:
            print "Updates were found"
            # todo: rename command.K to avoid any inadvetant usage of index.K ?
        else:
            print "No Updates found"
        if options.use_ihook: print '%ENDPOLE'
        

        # Verify command file passed in args
        if options.command_file:
            if os.path.exists(options.command_file):
                options.command_file = os.path.abspath(options.command_file)
            elif os.path.exists(os.path.join('/var/radmind/client',options.command_file)):
                options.command_file = os.path.join('/var/radmind/client',options.command_file)
            elif os.path.exists(os.path.join('/var/radmind/client/',options.command_file + ".K")):
                options.command_file = os.path.join('/var/radmind/client',options.command_file + ".K")
            else:
                raise Usage('specified command not found - leave off for default/auto')
        # if no command file arg - options.command_file will be false - use several methods to find one
        if not options.command_file:
            # check for local settings file
            config = '/Library/Management/radmind-config'
            if os.path.exists(config):
                value =  open(config).read().strip()
                if os.path.exists(value):
                    options.command_file = value
                elif os.path.exists(os.path.join('/var/radmind/client/',value)):
                    options.command_file = os.path.join('/var/radmind/client/',value)
                elif os.path.exists(os.path.join('/var/radmind/client/',value + ".K")):
                    options.command_file = os.path.join('/var/radmind/client/',value + ".K")
        if not options.command_file:
            # check for a host specific command file
            host = re.sub('\.local$','',os.uname()[1])
            candidate = os.path.join('/var/radmind/client',host.lower() + ".K")
            if os.path.exists(candidate):
                options.command_file = candidate
        if not options.command_file:
            # check for HW address based command file
            hw_address = sh ('ifconfig en0 | awk \'/ether/ { gsub(":", ""); print $2 }\'')
            candidate = os.path.join('/var/radmind/client',hw_address.strip() + ".K")
            if os.path.exists(candidate):
                options.command_file = candidate
        if not options.command_file:
            # check for a machine_type specific command file
            machine_type = sh("system_profiler SPHardwareDataType | grep 'Model Name' | awk '{print $3}'")
            candidate = os.path.join('/var/radmind/client',machine_type.lower().strip() + ".K")
            if os.path.exists(candidate):
                options.command_file = candidate
        if not options.command_file:
            # see if default file exists
            if os.path.exists(default_command):
                options.command_file = default_command
            elif os.path.exists(os.path.join('var/radmind/client',default_command)):
                options.command_file = os.path.join('var/radmind/client',default_command)
            else:
                raise RuntimeError('a suitable command file could not be located')
        # todo: could add a default read to check another location
        if not options.use_ihook:
            print "using command file: " + options.command_file

        #check pre_update directories
        for comp_path in pre_update_dirs:
            pass
            # return_value = sh('fsdiff %s %s' % (fsdiff_flags,comp_path))
            # todo:  check to see if this script is being replaced - and respawn
            # in author's environment, it is a network hosted script

        #check tool dependencies

        links_to_create = {}
        for binary in tools_needed:
            if os.path.islink(binary):
                # grab reference to target if tool is link - don't think this is required as none I'm interested in are ever links???
                dest = os.path.realpath(binary)
                links_to_create[os.path.basename(binary)] = os.path.basename(dest)
                if not dest in tools_needed: tools_needed.append(dest)
            # check for dynamically loaded lib dependencies, and load them into the correct relative path
            sh_output = sh("otool -L %s" % binary)
            for line in sh_output.split('\n')[1:]:
                #skip first header line of otool output that begins with binary
                dependency = re.findall('^\s*([^ ]*)',line)[0] # the first item on the line minus any leading whitespace
                if not dependency in tools_needed: tools_needed.append(dependency)
            # note that this loop will continue with newly appended dependencies

        # if a tool is located in a framework, consider the entire 
        # - containing framework as the dependency
        tools_needed = [re.sub('(?<=\.framework).*','',t) for t in tools_needed]
        # remove any duplicate frameworks
        tools_needed = uniq (tools_needed)
        if not options.use_ihook:
            print "final list of tools needed:"
            print tools_needed

        clear_ls_cache = False
        clear_components_cache = False
        rebuild_kernel_caches = False

        tools_copied = []
        # print ['fsdiff'] + fsdiff_flags + ['-K',options.command_file] + [radmind_comparison_path]
        if options.use_ihook:
            print "%ENDPOLE"
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'fsdiff.tif')
        print "Scanning File System for changes"
        return_value = call(['fsdiff'] + fsdiff_flags + ['-K',options.command_file] + [radmind_comparison_path])
        # todo: check return value and bail if needed        

        if not options.use_ihook:
            print "checking whether critical tools are being replaced"
        f = open(applicable_transcript)
        for line in f:
            # pre-compiling patterns not really needed since re module caches patterns for you
            if re.search('com\.apple\.LaunchServices',line): clear_ls_cache = True
            if re.search('System/Library/(Components|QuickTime)',line): clear_components_cache = True
            if re.search('System/Library/Extensions',line): rebuild_kernel_caches = True
            if re.search(dyld_path,line): dyld_being_replaced = True
            for tool in tools_needed:
                if tool not in tools_copied:                    
                    if re.search(re.escape(tool),line):
                        return_code = call('ditto %s %s' % (tool, os.path.join(tools_dir,tool.lstrip('/'))),shell=True)
                        # todo:  if return code not 0 raise error
                        tools_copied.append(tool)
                        if os.path.abspath(radmind_root) == "/":
                            options.reboot_after = True
        f.close()

        if (clear_ls_cache or rebuild_kernel_caches or dyld_being_replaced or tools_copied) and \
            os.path.abspath(radmind_root) == "/":
            options.reboot_after = True
        if not options.use_ihook:
            print 'tools copied so far:'
            print tools_copied
        
        if dyld_being_replaced:
            return_code = call('ditto %s %s' % (dyld_path,os.path.join(tools_dir,dyld_path.lstrip('/'))),shell=True)
            # todo:  if return code not 0 raise error
            # Copy any tool that wasn't already copied
            for tool in tools_needed:
                if tool not in tools_copied:
                    print 'copy ' + tool
                    return_code = call('ditto %s %s' % (tool, os.path.join(tools_dir,tool.lstrip('/'))),shell=True)
                    # todo:  if return code not 0 raise error

        # Embedded frameworks won't be found by dyld unless they are linked to TOOL_DIR
        for item in os.listdir(tools_dir):
            if item.endswith('framework'):
                for sub in os.listdir(os.path.join(item,"Frameworks")):
                    os.symlink(sub,os.path.join(tools_dir,os.path.basename(sub)))

        # # Make sure libraries can be found by any install name
        # foreach my $binary (keys %links_to_create) {
        #     -e "$TOOL_DIR/$links_to_create{$binary}" and symlink "$links_to_create{$binary}", "$TOOL_DIR/$binary";
        # }
        # create tools copies of linked binaries line 329 from commented wrapper
        # not sure why wouldn't do this when probing links..
        if not options.use_ihook:
            print " Finished copying tools"
        
        # could put this before lapply, or after chroot (would need to add to required tools list)...
        # if the path is in a positive, the touch may be reversed?
        if rebuild_kernel_caches and os.path.exists("./System/Library/Extensions"):
            call("touch ./System/Library/Extensions".split(' '))
        
        # shouldn't need this again here - but debugging:
        # os.chdir(radmind_root)
        if options.use_ihook:
            print "%ENDPOLE"
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'lapply.tif')
        print "Applying system updates..."
        return_value = call(['lapply'] + lapply_flags)
        
        if options.use_ihook: print "%ENDPOLE"

        if return_value != 0:
            # lapply failed
            # todo:  raise exception or log
            pass
    
        print "Post Update Actions"
        if clear_ls_cache:
            map(os.remove,glob('Library/Caches/com.apple.LaunchServices*'))
        if clear_components_cache:
            map(os.remove,glob('System/Library/Caches/com.apple.Components*'))  
        if rebuild_kernel_caches and os.path.exists("System/Library/Extensions"):
            # python analog of touch
            os.utime("System/Library/Extensions",None)
        # the original variations of this script had a subroutine that would fork the process in order
        # to set env variables and chroot - since that is only done below in two places, I do this 
        # for the main process.
        if dyld_being_replaced:
            # don't know that I need this signal remapping from perl version?  see signal module if so
            # $SIG{INT} = $SIG{TERM} = $SIG{HUP} = $SIG{__DIE__} = $SIG{__WARN__};
            os.chroot (tools_dir)
            # the following line not needed because all tools and libs were copied to relative path in chrooted dir
            # os.environ['PATH'] = os.environ['DYLD_LIBRARY_PATH'] = os.environ['DYLD_FRAMEWORK_PATH'] = '/'

        # todo:  post install actions set up as a one time startup item
        
        # bless the boot target:
        # todo - should the setboot part be an option?
        # boot_loc = os.path.join(radmind_root,'System/Library/CoreServices/')
        # bless_cmd = ['bless','--folder', boot_loc, '--bootefi', '--bootinfo', '--setBoot']
        # print bless_cmd
        # call(bless_cmd)
        
        if options.reboot_after:
            print 'restarting now'
            call("reboot")
            
        else:
            from shutil import rmtree
            rmtree(temp_dir,ignore_errors=True)
    except Usage, err:
        print >> sys.stderr, sys.argv[0].split("/")[-1] + ": " + str(err.msg)
        print >> sys.stderr, "\t for help use --help"
        return 2
    except RadmindError, msg:
        sys.stderr.write(str(msg))
if __name__ == '__main__':
    main()

