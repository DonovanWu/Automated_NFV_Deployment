'''
Assuming setup is done, use this program to automate certain processes
and interact with user.
Usage:
$ sudo python ui.py
'''

import os, select, time
from utils import *

################
## child bash ##
################

BUF_SIZE = 65536

toshell_read, toshell_write = os.pipe()
fromshell_read, fromshell_write = os.pipe()
polling_pool = None

# TODO: maybe set up another thread for polling output

#############
## console ##
#############

commands = {
	"help" : {
		"description" : "Show help message",
		"func_name" : "show_help"
	},
	"exit" : {
		"description" : "Exit console",
		"func_name" : "exit_console"
	},
	"demo" : {
		"description" : "Demonostrate parsing a table",
		"func_name" : "demo_parse_table"
	}
}
console_running = True

def main():
	global polling_pool
	
	pid = os.fork()
	if pid < 0:
		raise Exception("fork failed")

	if pid == 0:
		# child process
		print "Starting shell in child process..."
		cmd = ['/bin/bash']

		# redirects child bash's input to be given by 'toshell'
		# and redirects child bash's stdout and stderr to be read from 'fromshell'
		os.dup2(toshell_read, 0)
		# os.dup2(fromshell_write, 1)
		os.close(1)
		os.close(2)
		os.dup(fromshell_write)
		os.dup(fromshell_write)
		os.close(fromshell_write)

		os.execvp(cmd[0], cmd)
	else:
		# parent process
		polling_pool = select.poll()
		polling_pool.register(fromshell_read)
			#select.POLLIN | select.POLLERR | select.POLLHUP

	# wait for child process to start...
	time.sleep(0.5)

	# demo()
	init()
	# demo_parse_table()

	while console_running:
		print '$',
		user_input = raw_input()
		func = _get_cmd_func(user_input)
		if func != None:
			func()

###########################
## child bash comm funcs ##
###########################

def give_command(cmd):
	nbytes = os.write(toshell_write, '%s\n' % cmd)
	return nbytes

def poll_output(timeout=5000):
	for fd, event in polling_pool.poll(timeout):
		output = os.read(fd, BUF_SIZE)
		return output
	return ""

def get_returncode():
	clear_historical_outputs()
	give_command("echo $?")
	output = poll_output(timeout=1000)
	return int(output)

# timeout will be at least how long we will have to wait
def poll_all_outputs(timeout=5000):
	has_output = True
	historical_output = ""
	while has_output:
		has_output = False
		output = poll_output(timeout)
		if len(output) > 0:
			has_output = True
			if len(historical_output) == 0:
				historical_output = output
			else:
				historical_output += "\n%s" % output
	return historical_output

# just swiftly poll all outputs
def clear_historical_outputs():
	# Assumes historical outputs are ready to send
	# on child's stdout or stderr
	return poll_all_outputs(100)

###############################
## console interaction funcs ##
###############################

def show_help():
	for opt in commands:
		print "%-16s%s" % (opt, commands[opt]['description'])

def exit_console():
	global console_running
	console_running = False
	# waitpid?

def _get_cmd_func(cmd):
	if type(cmd) != str or cmd not in commands:
		print "%s: command not found" % cmd
		return None
	else:
		return globals().get(commands[cmd]['func_name'])

##############
## ui funcs ##
##############

def init():
	print "Initialzing UI..."
	cmd_seq = [
		'sudo su - stack',
		'cd devstack',
		'source openrc'
	]
	for cmd in cmd_seq:
		give_command(cmd)
	
	print poll_output()
	# print "Return code: %d" % get_returncode()

def demo():
	demo_cmds = ['sudo su - stack', 'whoami', 'cd devstack', 'ls']
	for cmd in demo_cmds:
		give_command(cmd)
		has_output = False
		for fd, event in polling_pool.poll(100):
			has_output = True
			output = os.read(fd, BUF_SIZE)
			print "Command '%s' gives output:" % cmd
			print output
		if not has_output:
			print "Command '%s' gives no output..." % cmd

def demo_parse_table():
	print "Command: openstack flavor list"
	print "Waiting for response..."
	give_command('openstack flavor list')
	output = os.read(fromshell_read, BUF_SIZE)
	print "Received output:"
	print output
	print "Parsed object:"
	print parse_openstack_table(output)

##########
## main ##
##########

if __name__ == '__main__':
	main()
