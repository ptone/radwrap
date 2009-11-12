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
import logging, logging.handlers
# import getopt
from optparse import OptionParser,OptionGroup
from subprocess import Popen, PIPE,call
from glob import glob
import re
import shutil


LOGLEVELS = {'debug': logging.DEBUG,
          'info': logging.INFO,
          'warning': logging.WARNING,
          'error': logging.ERROR,
          'critical': logging.CRITICAL}
          
os.environ['PATH'] = '/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin'


class Config(dict):
    """Example of overloading __getatr__ and __setattr__
    This example creates a dictionary where members can be accessed as attributes
    """
    def __init__(self):
        import ConfigParser
        config_paths = (
            '/etc/radutil.cfg',
            '/usr/local/etc/radutil.cfg',
            '/Library/Preferences/radutil.cfg',
        )
        base_defaults = {
            'rad_dir':'/var/radmind/',
            'default_k_excludes': '',
            'case_sensitive': False,
            'checksum': 'sha1',
            'fsdiffpath': '.',
            'server': 'radmind',
            'port': '6222',
        }
        configparser = ConfigParser.SafeConfigParser(base_defaults)
        configparser.read(config_paths)
        try:
            base_defaults.update(dict(configparser.items('radutil')))
        except ConfigParser.NoSectionError:
            pass
        if 'default_k_excludes' in base_defaults:
            base_defaults['default_k_excludes'] = base_defaults['default_k_excludes'].split()
        dict.__init__(self, base_defaults)
        self.__initialised = True
        # after initialisation, setting attributes is the same as setting an item

    def __getattr__(self, item):
        """Maps values to attributes.
        Only called if there *isn't* an attribute with this name
        """
        try:
            return self.__getitem__(item)
        except KeyError:
            raise AttributeError(item)

    def __setattr__(self, item, value):
        """Maps attributes to values.
        Only if we are initialised
        """
        if not self.__dict__.has_key('_attrExample__initialised'):  # this test allows attributes to be set in the __init__ method
            return dict.__setattr__(self, item, value)
        elif self.__dict__.has_key(item):       # any normal attributes are handled normally
            dict.__setattr__(self, item, value)
        else:
            self.__setitem__(item, value)


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

def get_logger():
    logger = logging.Logger('radwrap')
    h = logging.handlers.RotatingFileHandler('/var/log/radmindWrapper.log',maxBytes=50000,backupCount=5)
    f = logging.Formatter("%(asctime)s " + \
        "%(levelname)s\t%(message)s")
    h.setFormatter (f)
    h.setLevel (logging.DEBUG)
    logger.addHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    # use of debug level is for console printing - used to show or hide non-ihook friendly messages
    sh.setLevel (logging.INFO)
    logger.addHandler(sh)
    return logger
    
def search_file (value,rad_dir='/var/radmind/client/'):
    if os.path.exists(value):
        return os.path.abspath(value)
    elif os.path.exists(os.path.join(rad_dir,value)):
        return os.path.join(rad_dir,value)
    elif os.path.exists(os.path.join(rad_dir,value + ".K")):
        return os.path.join(rad_dir,value + ".K")
    return False
    
def get_directives(index_name='command'):
    directives = {}
    index_file = search_file (index_name)
    # looks for lines like:
    # #radwrap foo some.K
    if index_file:
        for line in open(index_file):
            if line[0:8].lower() == "#radwrap":
                k,v = line.strip().split()[1:]
                f = search_file (v)
                if f:
                    directives[k] = f
    return directives

def select_command_file(value, directives, rad_dir='/var/radmind/client/'):
    if value in directives:
        return directives[value]
    else:
        return search_file(value)

def main(argv=None):
    config = Config()
    logger = get_logger()
    
    if argv is None:
        argv = sys.argv
    try:
        #settings
        
        temp_dir = os.path.join(tempfile.gettempdir(),'radwrap')
        tools_dir = os.path.join(temp_dir,'tools')
        applicable_transcript = os.path.join(temp_dir,'applicable.T')
        dyld_path = '/usr/lib/dyld'
        dyld_being_replaced = False

        # User replaceable settings:
        # ,'/usr/sbin/bless' - doesn't work from inside chroot
        tools_needed = ['/bin/sh', '/sbin/reboot','/sbin/halt'] 
        pre_update_dirs = ('/usr/local/bin', '/Library/Management')
        radmind_server = config.server
        ihook_image_directory = '/Network/Library/management08/radmind/images/'
        radmind_port = config.port
        radmind_comparison_path = config.fsdiffpath
        ktcheck_flags = [   '-C', 
                            '-c', config.checksum, 
                            '-h', config.server, 
                            '-p', config.port]
                            
        fsdiff_flags = [    '-A',
                            '-I', 
                            '-o', applicable_transcript, 
                            '-%' ]
                            
        lapply_flags = [    '-I',
                            '-F',
                            '-C', 
                            '-i', 
                            '-%', 
                            '-h',config.server, 
                            '-p',config.port, 
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
        
        if not options.use_ihook:
            # this is a bit hackish, as there is no good way to address a already attached handler
            # basically what this does is allow all logs to flow to stdout if ihook is not being used
            logger.handlers[1].setLevel(logging.DEBUG)
        
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
            # @@ todo:  test whether error is because already exists
            raise
        
        os.chdir(radmind_root)
        #disable sleep
        # @@ need to reset at end - or is it reset at reboot?
        # sh('pmset sleep 0')
        # todo:  disable spotlight and time machine during lapply
        
        if options.use_ihook: 
            print '%BECOMEKEY'
            print '%UIMODE AUTOCRATIC'    
            print '%WINDOWLEVEL HIGH'
            print '%BEGINPOLE'
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'ktcheck.tif')
            ktcheck_flags.append('-q')
        logger.info( 'Checking for changes from server')
        # we need to check first because this fetches a potential matching K file that wasn't
        # already on the client
        
        # print ktcheck_flags
        return_value = call(['ktcheck'] + ktcheck_flags)
        # return_value = call(['ktcheck'] + ktcheck_flags,shell=True)
        if return_value > 1:
            logger.error('ktcheck returned %s, exiting' % return_value)
            raise RadmindError, 'ktcheck returned %s' % return_value
        if return_value == 1:
            logger.info( "Updates were found")
            # todo: rename command.K to avoid any inadvetant usage of index.K ?
        else:
            logger.info( "No Updates found")
        if options.use_ihook: print '%ENDPOLE'
        

        # Verify command file passed in args
        if options.command_file:
            options.command_file = search_file (options.command_file)
            if not options.command_file:
                raise Usage('specified command not found - leave off for default/auto')
        # if no command file arg - options.command_file will be false - use several methods to find one
        # @@ should use a list of callables
        if not options.command_file:
            # check for local settings file
            if 'radwrap' in config:
                options.command_file = search_file(config.radwrap)
        if not options.command_file:
            directives = get_directives()
            # check for a host specific command file
            host = re.sub('\.local$','',os.uname()[1]).lower()
            options.command_file = select_command_file (host,directives)
        if not options.command_file:
            # check for HW address based command file
            hw_address = sh ('ifconfig en0 | awk \'/ether/ { gsub(":", ""); print $2 }\'').strip()
            options.command_file = select_command_file (hw_address,directives)
        if not options.command_file:
            # check for a machine_type specific command file
            machine_type = sh("system_profiler SPHardwareDataType | grep 'Model Name' | awk '{print $3}'").lower().strip()
            options.command_file = select_command_file (machine_type,directives)
        if not options.command_file:
            # see if default file exists
            options.command_file = search_file(default_command)
            if not options.command_file:
                logger.error('a suitable command file could not be located')
                sys.exit(1)
        
        logger.debug("using command file: " + options.command_file)

        #check pre_update directories
        for comp_path in pre_update_dirs:
            pass
            # this is a stub of a step from the perl version that is not implemented here
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
            try:
                sh_output = sh("otool -L %s" % binary)
            except:
                logger.error ("Could not run otool - is it installed properly?")
                sys.exit()
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
        logger.debug( "final list of tools needed:")
        map (logger.debug, tools_needed) 

        clear_ls_cache = False
        clear_components_cache = False
        rebuild_kernel_caches = False

        tools_copied = []
        # print ['fsdiff'] + fsdiff_flags + ['-K',options.command_file] + [radmind_comparison_path]
        if options.use_ihook:
            print "%ENDPOLE"
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'fsdiff.tif')
        logger.info( "Scanning File System for changes")
        return_code = call(['fsdiff'] + fsdiff_flags + ['-K',options.command_file] + [radmind_comparison_path])
        if return_code:
            logger.error ("fsdiff failed with return value: %s" % return_code)
            sys.exit(return_code)
        logger.debug( "checking whether critical tools are being replaced")
        # @@ this may be a lot of work to avoid a reboot - maybe just make reboots mandatory...?
        for line in open(applicable_transcript):
            # pre-compiling patterns not really needed since re module caches patterns for you
            if re.search('com\.apple\.LaunchServices',line): clear_ls_cache = True
            if re.search('System/Library/(Components|QuickTime)',line): clear_components_cache = True
            if re.search('System/Library/Extensions',line): rebuild_kernel_caches = True
            if re.search(dyld_path,line): dyld_being_replaced = True
            for tool in tools_needed:
                if tool not in tools_copied:                    
                    if re.search(re.escape(tool),line):
                        return_code = call('ditto %s %s' % (tool, os.path.join(tools_dir,tool.lstrip('/'))),shell=True)
                        if return_code:
                            logger.error ("ditto failed with return value: %s for: %s" % (return_code,tool))
                            sys.exit(return_code)
                        tools_copied.append(tool)
                        if os.path.abspath(radmind_root) == "/":
                            options.reboot_after = True

        if (clear_ls_cache or rebuild_kernel_caches or dyld_being_replaced or tools_copied) and \
            os.path.abspath(radmind_root) == "/":
            options.reboot_after = True
        logger.debug( 'tools copied so far:')
        map (logger.debug,tools_copied)
        
        if dyld_being_replaced:
            return_code = call('ditto %s %s' % (dyld_path,os.path.join(tools_dir,dyld_path.lstrip('/'))),shell=True)
            if return_code:
                logger.error ("ditto failed with return value: %s for: %s" % (return_code,tool))
                sys.exit(return_code)            
            # Copy any tool that wasn't already copied
            for tool in tools_needed:
                if tool not in tools_copied:
                    logger.debug( 'copy ' + tool)
                    return_code = call('ditto %s %s' % (tool, os.path.join(tools_dir,tool.lstrip('/'))),shell=True)
                    if return_code:
                        logger.error ("ditto failed with return value: %s for: %s" % (return_code,tool))
                        sys.exit(return_code)

        # Embedded frameworks won't be found by dyld unless they are linked to TOOL_DIR
        for item in os.listdir(tools_dir):
            if item.endswith('framework'):
                for sub in os.listdir(os.path.join(item,"Frameworks")):
                    os.symlink(sub,os.path.join(tools_dir,os.path.basename(sub)))

        # # Make sure libraries can be found by any install name
        # not implemented - no needed
        # foreach my $binary (keys %links_to_create) {
        #     -e "$TOOL_DIR/$links_to_create{$binary}" and symlink "$links_to_create{$binary}", "$TOOL_DIR/$binary";
        # }
        # create tools copies of linked binaries line 329 from commented wrapper
        # not sure why wouldn't do this when probing links..
        logger.debug( " Finished copying tools")
        
        # could put this before lapply, or after chroot (would need to add to required tools list)...
        # if the path is in a positive, the touch may be reversed?
        if rebuild_kernel_caches and os.path.exists("./System/Library/Extensions"):
            call("touch ./System/Library/Extensions".split(' '))
        
        # shouldn't need this again here - but debugging:
        # os.chdir(radmind_root)
        if options.use_ihook:
            print "%ENDPOLE"
            print '%BACKGROUND ' + os.path.join(ihook_image_directory,'lapply.tif')
        logger.info( "Applying system updates...")
        return_code = call(['lapply'] + lapply_flags)

        if options.use_ihook: print "%ENDPOLE"

        if return_code:
            codes = ('','An error occurred, system was modiﬁed.','An error occurred, system was not modiﬁed.')
            logger.error (codes[return_code])
            sys.exit(return_code)

    
        logger.info( "Post Update Actions")
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
            # bless boot target
            
        
        if options.reboot_after:
            # @@ allow an option for whether to restart or shutdown
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
        # @@ really not sure I need the outer try - need to focus try blocks in code better
        sys.stderr.write(str(msg))
if __name__ == '__main__':
    main()

