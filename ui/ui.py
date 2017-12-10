'''
Assuming setup is done, use this program to automate certain processes
and interact with user.
Usage:
$ sudo python ui.py <args>
Arguments:

'''

import os, sys, select, time, argparse, traceback, subprocess
import configs, utils, visualizer

################
## child bash ##
################

BUF_SIZE = 65536

toshell_read, toshell_write = os.pipe()
fromshell_read, fromshell_write = os.pipe()
polling_pool = None

home_dir = ""

# TODO: maybe set up another thread for polling output??

#############
## console ##
#############

commands = {
	"help" : {
		"description" : "Show help message",
		"usage" : "help [command]\n" + \
				"* HINT: If a command is specified, show help message of that command.",
		"func_name" : "show_help"
	},
	"exit" : {
		"description" : "Exit console",
		"func_name" : "exit_console"
	},
	"conf" : {
		"description" : "Show current configuration (in json format)",
		"func_name" : "show_config"
	},
	"exec" : {
		"description" : "Send a command to child bash and execute it",
		"usage" : "exec command",
		"func_name" : "send_cmd_and_exec"
	},
	"list" : {
		"description" : "A few things you can list...",
		"usage" : "list option\noptions:\n" + \
				"\t%-8s%s" % ("ip", "shows a list of instance names mapped to floating ip\n") + \
				"\t%-8s%s" % ("server", "shows list of servers"),
		"func_name" : "list_things"
	},
	"visl" : {
		"description" : "Visualize server usage",
		"usage" : "visl instance_name",
		"func_name" : "viz_usage"
	},
	"demo" : {
		"description" : "Demonostrate how utils.parse_openstack_table() parse a table to a json object (for future developers)",
		"func_name" : "demo_parse_table"
	},
	"gtfo" : {
		"description" : "(For testing) Raise an error on console ui",
		"func_name" : "force_quit_jk"
	},
	"deploy" : {
		"description" : "Deploy (more) VMs by given SLA file",
		"usage" : "deploy sla_file_name",
		"func_name" : "usrcmd_deploy_vm"
	},
	"delete" : {
		"description" : "Delete created VM instance",
		"usage" : "delete --vm vm_name\n" + \
				"\tDelete VM of given VM name. Must be in current configuration.\n" + \
				"delete --instance instance_name [--key key_name]\n" + \
				"\tDelete server instance by explicitly giving instance and key name." + \
				"If key name isn't given, use default key name.\n" + \
				"delete --all\n" + \
				"\tDelete all VMs in current configuration\n" + \
				"* HINT: You can check current configuration by using 'conf' command",
		"func_name" : "usrcmd_delete_vm"
	},
	"snapshot" : {
		"description" : "Create and replace snapshots of VM instances",
		"usage" : "snapshot option\noptions:\n" + \
				"\t%-40s%s" % ("list", "List images\n") + \
				"\t%-40s%s" % ("create instance_name snapshot_name", "Create a snapshot of an instance\n") + \
				"\t%-40s%s" % ("delete snapshot_name", "Delete a snapshot of an instance\n") + \
				"\t%-40s%s" % ("replace instance_name snapshot_name", "Replace a current VM with a snapshot"),
		"func_name" : "usrcmd_snapshot"
	}
}
console_running = True
work_dir = ""

# new variables
keystore_dir = ""
keyName = ""
ip = ""
command_to_run = ""

def main():
	global polling_pool

	# parse args
	args = parse_args()

	# process args to intialize configs module
	# will raise exception if input is ill-formatted
	if args.sla != None:
		configs.parse_sla_config(args.sla)

	# fork child bash
	pid = os.fork()
	if pid < 0:
		raise RuntimeError("fork failed")

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

	# first commands to child bash
	init()

	# check available RAM and warn user if RAM is not enough
	if args.sla != None:
		free_ram = get_available_ram()
		if free_ram < 0:
			utils.print_warning("[WARNING] An error occurred when checking RAM. Please check available RAM by yourself.")
		elif free_ram < 4096:
			utils.print_error("WARNING!!! Less than 4GB of RAM available!")
			# too long (lol)
			print "%s %s %s" % ("It is recommended to have at least 4GB of free RAM for VM deployment.",
				"Insufficient RAM may result in deployment error.",
				"Do you wish to continue (y/n)?"),
			s = raw_input()
			if s[0].lower() == 'n':
				raise SystemExit("Aborted.")

	# check if openstack is up at all
	give_command('openstack flavor list') # should have some default flavors present
	output = poll_output(-1)
	flavor_table = None
	if len(output) == 0:
		raise RuntimeError("Openstack timed out!")
	else:
		rc = get_returncode()
		flavor_table = utils.parse_openstack_table(output)
		if rc != 0 or flavor_table == None:
			utils.print_error(output)
			raise RuntimeError("Openstack is not up! Please make sure you have executed 'stack.sh' as stack user!")

	# create enb flavor if not present
	flavor_list = [entry['ID'] for entry in flavor_table]
	if 'enb' not in flavor_list:
		give_command('echo $OS_USERNAME')
		prev_username = poll_output(-1)
		if len(prev_username) == 0:
			raise RuntimeError("Unable to get environment variable: $OS_USERNAME")
		print "Switching to openstack admin user..."
		give_command('source openrc admin')
		print poll_output()
		print "Creating openstack flavor for eNodeB nodes..."
		give_command('openstack flavor create --public enb_flavor --id enb --ram 4096 --disk 40 --vcpus 4')
		print poll_output(-1)

		print "Restoring previous openstack user: %s" % prev_username
		give_command('source openrc %s' % prev_username)
		print poll_output()


	# process according to SLA specification
	print "Processing SLA configuration..."
	for vm in configs.sla_configs:
		deploy_vm(vm)

	# user console
	while console_running:
		print '$',
		user_args = raw_input().split()

		if len(user_args) > 0:
			try:
				cmd = user_args[0]
				cmd_args = user_args[1:] if len(user_args) > 1 else []
				func = get_cmd_func(cmd)
				if func != None:
					func(cmd_args)
			except:
				# print in red color
				print utils.bcolors.RED,
				traceback.print_exc(file=sys.stdout)
				print utils.bcolors.NC

##############################
## child bash communication ##
##############################

def give_command(cmd):
	nbytes = os.write(toshell_write, '%s\n' % cmd)
	return nbytes

def poll_output(timeout=5000):
	for fd, event in polling_pool.poll(timeout):
		output = os.read(fd, BUF_SIZE)
		return output.strip()

	# TODO: add waitpid to see if child exits due to error

	return ""

# returns output parsed to int
# if output is empty, returns -1
# if output cannot be parsed, prints a warning message and returns the original output
def get_returncode():
	clear_historical_outputs()
	give_command("echo $?")
	output = poll_output(timeout=1000)
	if output.isdigit():
		return int(output)
	elif len(output) == 0:
		return -1
	else:
		utils.print_warning("[WARNING] get_returncode() returned other output: %s" % output)
		return output

# timeout will be at least how long we will have to wait
def poll_all_outputs(timeout=5000, init_wait=2000, suppress_msg=False):
	if not suppress_msg:
		print "Please wait patiently as we're polling potential outputs..."

	time.sleep(init_wait / 1000.0)

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

# print to screen as polling all potential output
def responsive_poll(timeout=5000):
	has_output = True
	while has_output:
		has_output = False
		output = poll_output(timeout)
		if len(output) > 0:
			has_output = True
			print output

# assumes all historical outputs are ready to send on child's stdout or stderr
def clear_historical_outputs():
	# just swiftly poll all outputs...
	return poll_all_outputs(timeout=200, init_wait=0, suppress_msg=True)

###########################
## console command funcs ##
###########################

def show_help(args=[]):
	if len(args) == 0:
		# list all commands available
		cmd_list = list(commands.keys())
		cmd_list.sort(key=lambda item: (len(item), item))  # sort by length, then alphabetical order
		for opt in cmd_list:
			print "%-16s%s" % (opt, commands[opt]['description'])
		print "Use 'help command_name' to check usage of each command."
	else:
		# show help for specified command
		for arg in args:
			if commands.has_key(arg):
				if commands[arg].has_key('usage'):
					print "Usage - %s:" % arg
					print commands[arg]['usage']
				else:
					print "(No usage information for '%s'. Probably doesn't take any arguments.)" % arg
			else:
				print "--Command '%s' doesn't exist!--" % arg

def exit_console(args=[]):
	global console_running
	console_running = False
	give_command("exit")
	time.sleep(0.25)
	# waitpid?

def force_quit_jk(args=[]):
	raise RuntimeError("No, you can't.")

def show_config(args=[]):
	print utils.format_dict(configs.sla_configs)

def send_cmd_and_exec(args=[]):
	if len(args) == 0:
		# wrong usage
		show_help(['exec'])
		return

	if type(args) == list:
		args = ' '.join(args)
	if type(args) == str:
		print "Sending command to child bash: %s" % args
		give_command(args)
		# responsive_poll()
		print poll_output()

def list_things(args=[]):
	if len(args) != 1:
		# wrong usage
		show_help(['list'])
		return

	if args[0] == 'ip':
		# list ip
		ip_map = get_floating_ip_map()
		if ip_map != None:
			for instance_name, floating_ip in ip_map:
				give_command("openstack server show %s" % instance_name)
				output = poll_output(-1)
				table = utils.parse_openstack_table(output)
				key_name = None
				if table != None:
					for entry in table:
						if entry['Field'] == 'key_name':
							key_name = entry['Value']
							break

				ssh_cmd = ""
				if key_name != None:
					ssh_cmd = "sudo ssh -oStrictHostKeyChecking=no -i /opt/stack/keys/%s ubuntu@%s" % (key_name, floating_ip)
					scp_cmd = "sudo scp -oStrictHostKeyChecking=no -i /opt/stack/keys/%s src_file ubuntu@%s:dst_file" % (key_name, floating_ip)
				# print "%-16s%-16sssh command:   %s" % (instance_name, floating_ip, ssh_cmd)
				print "%-16s%-16s\n\tssh command:   %s\n\tscp command:   %s" % (instance_name, floating_ip, ssh_cmd, scp_cmd)
		else:
			print ""
	elif args[0] == 'server':
		# list server
		give_command("openstack server list")
		print poll_output(-1)
	else:
		print "Illegal argument: %s" % args[0]


def usrcmd_snapshot(args=[]):
	if len(args) < 1:
		# wrong usage
		show_help(['snapshot'])
		return


	if args[0] == 'list':
		give_command("openstack image list")
		print poll_output(-1)

	elif args[0] == 'create':
		if len(args) != 3:
			print "Please specify the arguments"
		else:
			give_command('openstack server show %s' % args[1])
			output = poll_output(-1)
			if output == "No server with a name or ID of '%s' exists." % args[1]:
				print "Specify a correct server name"
				return
			else:
				table, output = get_table('openstack server list', both=True)
				server_id = [entry for entry in table if entry['Name'] == args[1]][0]['ID']


				print "Creating snapshot"
				give_command('nova image-create %s "%s"' % (server_id, args[2]))
				time.sleep(2)

				timebound = 60
				should_proceed = True
				start_time = time.time()
				while True:
					# time bound
					time_elapsed = time.time() - start_time
					if time_elapsed >= timebound:
						utils.print_warning("Giving up on waiting for snapshot '%s' to become active..." % args[2])
						break

					# check server status
					print "Checking status for snapshot '%s'..." % args[2]
					table, output = get_table('openstack image list', both=True)
					if table != None:
						status = [entry for entry in table if entry['Name'] == args[2]][0]['Status']
						print "Status: %s" % status
						if status == 'active':
							utils.print_pass("Snapshot '%s' successfully created!" % args[2])
							break
					elif status == 'error':
							should_proceed = False
							utils.print_error("Snapshot '%s' is in ERROR state!" % args[2])
							break

					time.sleep(5)

	elif args[0] == 'delete':
		if len(args) != 2:
			print "Please specify the arguments"

		give_command('openstack image show %s' % args[1])
		output = poll_output(-1)
		poll_all_outputs()
		if output == "Could not find resource %s" % args[1]:
			print "Image does not exist"
			return
		else:
			give_command('openstack image delete %s' % args[1])
			print "Snapshot deleted"


	else:
		print "Illegal argument: %s" % args[0]
		show_help(['snapshot'])
		return

def viz_usage(args=[]):
	if len(args) != 1:
		# wrong usage
		show_help(['visl'])
		return

	print "This function is not implemented yet!"

def usrcmd_deploy_vm(args=[]):
	if len(args) != 1:
		# wrong usage
		show_help(['deploy'])
		return

	new_vms = configs.parse_sla_config(args[0])
	for vm in new_vms:
		deploy_vm(vm)

def usrcmd_delete_vm(args=[]):
	parser = argparse.ArgumentParser()
	parser.add_argument('--all', action='store_true', required=False)
	parser.add_argument('--vm', action='store', required=False)
	parser.add_argument('--instance', action='store', required=False)
	parser.add_argument('--key', action='store', required=False)
	args = parser.parse_args(args)

	if args.all:
		if len(configs.sla_configs) == 0:
			utils.print_warning("Empty configuration! Nothing to delete!")
		vms = list(configs.sla_configs.keys())
		for vm in vms:
			delete_vm(vm)
	elif args.vm != None:
		delete_vm(args.vm)
	elif args.instance != None:
		key = '%s_key' % args.instance
		if args.key != None:
			key = args.key
		delete_server_instance(args.instance, key)
	else:
		# wrong usage
		show_help(['delete'])

###########################
## console backend funcs ##
###########################

def parse_args():
	# parse args
	parser = argparse.ArgumentParser()
	parser.add_argument('--sla', action='store', required=False,
		metavar='SLA_config_file', help='specify SLA config file')
	args = parser.parse_args()
	return args

def init():
	global home_dir, work_dir
	print "Initialzing console..."

	# get working directory of THIS SCRIPT (do not confuse)
	work_dir = subprocess.check_output(['pwd']).strip()

	give_command('sudo su - stack')
	give_command('pwd')
	home_dir = poll_output(timeout=1000)
	if len(home_dir) == 0:
		raise RuntimeError("[ERROR] Could not get home directory. Please restart and try again.")
	give_command('cd devstack')
	give_command('source openrc')    # may have output
	print poll_output(timeout=2000)

def deploy_vm(vm):
	utils.print_highlight("====Deploying %s====" % vm)
	cfg = configs.sla_configs[vm]
	configure_deployment(vm, cfg['vm_type'], cfg['deploy_config'])
	configure_oai(vm, cfg['vm_type'], cfg['oai_configs'])

def delete_vm(vm):
	if not  configs.sla_configs.has_key(vm):
		utils.print_error("There isn't a VM named '%s'" % vm)
		print "All available VM names:"
		print '\n'.join(configs.keys())
		return

	utils.print_highlight("====Deleting %s====" % vm)
	cfg = configs.sla_configs[vm]
	utils.print_highlight("Deleting vm: %s" % vm)
	if cfg['vm_type'] == "server":
		delete_server_instance(cfg['deploy_config']['INSTANCE_NAME'], cfg['deploy_config']['KEY_NAME'], vm)
	else:
		utils.print_warning("Cannot delete - Unknown or yet-to-implement VM type: %s" % cfg['vm_type'])

def delete_server_instance(instance_name, key_name, vm_name=None):
	# make sure we can entrust that given vm_name is correct

	cfgs = configs.sla_configs
	if vm_name == None or not cfgs.has_key(vm_name) or \
	not cfgs[vm_name]['deploy_config']['INSTANCE_NAME'] == instance_name or \
	not cfgs[vm_name]['deploy_config']['KEY_NAME'] == key_name:
		vm_name = None

	if vm_name == None:
		# try to find vm_name
		for vm in cfgs:
			dpl_cfg = cfgs[vm]['deploy_config']
			if dpl_cfg['INSTANCE_NAME'] == instance_name and dpl_cfg['KEY_NAME'] == key_name:
				vm_name = vm

	# get floating ip address, if any
	floating_ip = None
	ip_map = get_floating_ip_map()
	if ip_map != None:
		for server_name, ip in ip_map:
			if server_name == instance_name:
				floating_ip = ip
				break

	# delete server instance itself
	print "Removing server instance: %s" % instance_name
	give_command("openstack server delete %s" % instance_name)
	print poll_all_outputs()
	if get_returncode() == 0:
		utils.print_pass("SUCCESS")

	# delete associated keypair
	print "Removing associated keypair: %s" % key_name
	give_command("openstack keypair delete %s" % key_name)
	print poll_all_outputs()
	if get_returncode() == 0:
		utils.print_pass("SUCCESS")

	if floating_ip != None:
		# dissociate the known host
		print "Dissociate the known host..."
		give_command('sudo ssh-keygen -f "/root/.ssh/known_hosts" -R %s' % floating_ip)
		print poll_output(-1)

		# delete associated floating ip
		print "Removing floating ip: %s" % floating_ip
		give_command("openstack floating ip delete %s" % floating_ip)
		print poll_all_outputs()
		if get_returncode() == 0:
			utils.print_pass("SUCCESS")

	if vm_name != None:
		configs.sla_configs.pop(vm_name)

# returns an array of tuple (server_instance, floating_ip)
def get_floating_ip_map():
	clear_historical_outputs()
	give_command("openstack floating ip list")
	output = poll_output(-1)
	table_floating_ips = utils.parse_openstack_table(output)

	give_command("openstack server list")
	output = poll_output(-1)
	table_servers = utils.parse_openstack_table(output)

	if table_floating_ips != None and table_servers != None:
		ip_list = [(entry['Floating IP Address'], entry['Fixed IP Address']) for entry in table_floating_ips]
		ret = []
		for entry in table_servers:
			name = entry['Name']
			networks = entry['Networks']
			for floating_ip, fixed_ip in ip_list:
				if floating_ip in networks and fixed_ip in networks:
					ret.append((name, floating_ip))
		return ret
	else:
		utils.print_error("Failed to read server list or floating ip list.")
		return None

def get_cmd_func(cmd):
	if type(cmd) != str or cmd not in commands:
		print "%s: command not found" % cmd
		return None
	else:
		return globals().get(commands[cmd]['func_name'])

# returns None if given command doesn't return any output
def get_table(cmd, both=False):
	give_command(cmd)
	output = poll_output(-1)
	if both:
		return utils.parse_openstack_table(output), output
	else:
		return utils.parse_openstack_table(output)

# unit: MB; if an error occurs, returns -1
def get_available_ram():
	try:
		proc = subprocess.Popen(["free", "-m"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		out, err = proc.communicate()
		if proc.returncode == 0:
			'''
Example:
              total        used        free      shared  buff/cache   available
Mem:          15993       10097         321         158        5574        5244
Swap:         16336         511       15825
			'''
			# parsing table
			table = {}
			lines = out.strip().split('\n')
			titles = lines[0].split()
			for line in lines[1:]:
				line = line.split()
				table[line[0]] = line[1:]

			# getting ram
			free_ram = int(table['Mem:'][titles.index('free')])
			return free_ram
		else:
			return -1
	except:
		return -1

##############################
## configuration automation ##
##############################

def configure_deployment(vm_name, vm_type, deploy_config):
	if vm_type == "server":
		# TODO: testing if the server already exists?
		create_server(vm_name, deploy_config)

def configure_oai(vm_name, vm_type, oai_configs):
	global command_to_run
	source_file_path = []
	destination_file_path = []
	base_path_src = '%s/OAI_Scripts/' % work_dir
	base_path_dst = '/home/ubuntu/'

	for oai_opt in oai_configs:
		# oai_opt = eNodeB, ue, eNodeB_ue, hss, mme, spgw
		# oai_configs[oai_opt] = {}	--> dict for possible params in the future
		if oai_opt == "eNodeB":
			for fname in ['eNodeB.sh', 'enb.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "ue":
			for fname in ['UE.sh', 'ue.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "eNodeB_ue":
			for fname in ['UE_eNodeB.sh', 'enb.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "hss":
			for fname in ['HSS.sh', 'hss.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "mme":
			for fname in ['MME.sh', 'mme.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "spgw":
			for fname in ['SPGW.sh', 'spgw.conf']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst + fname)
		elif oai_opt == "epc":
			for fname in ['EPC.sh', 'hss.conf', 'mme.conf', 'spgw.conf', '*_expect.exp']:
				source_file_path.append(base_path_src + fname)
				destination_file_path.append(base_path_dst) # + fname)

		scp_command(source_file_path, destination_file_path)
		time.sleep(0.5)

		'''
		command_to_run = 'pidof apt-get | xargs kill -9'
		ssh_command(command_to_run)
		time.sleep(0.25)

		command_to_run = 'sudo bash %s' % destination_file_path
		ssh_command(command_to_run)
		'''

def ssh_command(command_to_run):

	# To remove the key from known host
	# sudo ssh-keygen -f "/root/.ssh/known_hosts" -R 172.24.4.6
	# sudo rm "/opt/stack/.ssh/known_hosts"

	ssh_cmd = 'sudo ssh -T -oStrictHostKeyChecking=no -i %s/%s ubuntu@%s \'%s\'' % (keystore_dir, keyName, ip, command_to_run)
	print ssh_cmd
	give_command(ssh_cmd)
	print poll_all_outputs()

def scp_command(source_file_path, destination_file_path):
	if type(source_file_path) == str and type(destination_file_path) == str:
		scp_cmd = 'sudo scp -oStrictHostKeyChecking=no -i %s/%s %s ubuntu@%s:%s' % (keystore_dir, keyName, source_file_path, ip, destination_file_path)
		print scp_cmd
		give_command(scp_cmd)
		print poll_all_outputs()
	elif type(source_file_path) == list and type(destination_file_path) == list and len(source_file_path) == len(destination_file_path):
		for src, dst in [(source_file_path[i], destination_file_path[i]) for i in xrange(len(source_file_path))]:
			scp_command(src, dst)

def create_server(vm_name, deploy_config):
	global keystore_dir, keyName, ip

	# create images folder
	img_dir = '%s/images' % home_dir
	utils.print_warning("Checking %s" % img_dir)
	if not os.path.isdir(img_dir):
		create_command = 'mkdir %s' % img_dir
		give_command(create_command)

	keystore_dir = '%s/keys' % home_dir
	if not os.path.isdir(keystore_dir):
		create_keystore_command = 'mkdir %s' % keystore_dir
		give_command(create_keystore_command)

	# check if image already exist
	give_command('openstack image show %s' % deploy_config['IMAGE_NAME'])
	output = poll_output(-1)
	utils.print_warning (output)
	if output == "Could not find resource %s" % deploy_config['IMAGE_NAME']:
		# Step 0: create image
		image_file = '%s/images/ubuntu-17.04.img' % home_dir
		if not os.path.isfile(image_file):
			rc = subprocess.call(['wget', '-O', image_file,
				'https://cloud-images.ubuntu.com/zesty/current/zesty-server-cloudimg-amd64.img'])
			# check return code: (may not be connected to internet)
		else:
			print "Image file: using exisiting file on disk: %s" % image_file

		cmd = 'openstack image create --unprotected --disk-format qcow2 --file %s %s' % (image_file,
			deploy_config['IMAGE_NAME'])
		print "Creating image... Please wait patiently:\n%s" % cmd
		give_command(cmd)
		print poll_output(-1) # will print image show
	elif output == "More than one resource exists with the name or ID '%s'" % deploy_config['IMAGE_NAME']:
		utils.print_warning("[WARNING] %s" % output)
	else:
		# image exists: don't re-create
		print "Using exisiting image '%s'" % deploy_config['IMAGE_NAME']

	# Step 1: Keypair
	if deploy_config["KEY_NAME"] == None or len(deploy_config["KEY_NAME"]) == 0:
		deploy_config["KEY_NAME"] = "%s_key" % deploy_config["INSTANCE_NAME"]

	# TODO: check if the user provides the path to its own key

	# check failed: create a new key
	give_command('rm -f ~/.ssh/id_rsa*')
	# output = poll_output(-1)
	time.sleep(0.25)
	give_command('ssh-keygen -q -N ""') # requires file name
	time.sleep(0.25)
	give_command('')	# use default
	time.sleep(0.25)
	# give_command('')	# possible overwrite
	print poll_all_outputs(timeout=3000, init_wait=0)
	# rc = get_returncode()
	# TODO: check return code???)
	# print "rc=%s" % rc 	# could be 1 if overwrite
	utils.print_warning("Creating keypair...")
	give_command('openstack keypair create --public-key ~/.ssh/id_rsa.pub %s' % deploy_config["KEY_NAME"])
	print poll_output(-1)

	# Copy the key to our keystore
	keyName = deploy_config["KEY_NAME"]
	give_command('cp -f ~/.ssh/id_rsa %s/%s' % (keystore_dir, deploy_config["KEY_NAME"]))
	#give_command('chmod 400 %s/%s' % (keystore_dir, deploy_config["KEY_NAME"]))

	# Step 2: Display keypair
	table, output = get_table('openstack keypair list', both=True)
	print "Keypair list:"
	print output
	# This piece of code is creating a lot of errors
	#newkey = [entry for entry in table if entry['Name'] == deploy_config['KEY_NAME']][0]
	#print "New key:"
	#print utils.format_dict(newkey)

	if deploy_config["SECURITY_GROUP_NAME"] != None:
		# Step 3
		sec_grp = deploy_config['SECURITY_GROUP_NAME']
		subnet_name = deploy_config['PRIVATE_SUBNET_NAME']

		print "Creating new security group: %s" % sec_grp

		give_command('openstack security group create %s' % sec_grp)
		print poll_output(-1)
		give_command('openstack security group rule create --proto icmp %s' % sec_grp)
		print poll_output(-1)
		give_command('openstack security group rule create --proto tcp --dst-port 22 %s' % sec_grp)
		print poll_output(-1)
		give_command('openstack security group rule create --protocol tcp --dst-port 80:80 --remote-ip 0.0.0.0/0 %s' % sec_grp)
		print poll_output(-1)
		give_command('openstack security group rule create --protocol tcp --dst-port 443:443 --remote-ip 0.0.0.0/0 %s' % sec_grp)
		print poll_output(-1)
		give_command('openstack subnet set --dns-nameserver 8.8.8.8 %s' % subnet_name)
		time.sleep(0.25)
		print poll_all_outputs()

	else:
		# Step 4
		print "Using default security group and private-subnet"

		give_command('openstack security group rule create --proto icmp default')
		print poll_output(-1)
		give_command('openstack security group rule create --proto tcp --dst-port 22 default')
		print poll_output(-1)
		give_command('openstack security group rule create --protocol tcp --dst-port 80:80 --remote-ip 0.0.0.0/0 default')
		print poll_output(-1)
		give_command('openstack security group rule create --protocol tcp --dst-port 443:443 --remote-ip 0.0.0.0/0 default')
		print poll_output(-1)
		give_command('openstack subnet set --dns-nameserver 8.8.8.8 private-subnet')
		time.sleep(0.25)
		print poll_all_outputs()

		deploy_config['SECURITY_GROUP_NAME'] = 'default'

	# Step 5: Parse netID
	print poll_all_outputs()
	table, output = get_table('openstack network list', both=True)
	print "Current network list:"
	print output
	net_id = [entry for entry in table if entry['Name'] == deploy_config['PRIVATE_NETWORK_NAME']][0]['ID']

	# Step 6: Create instance
	# check if server exists already
	give_command('openstack server show %s' % deploy_config['INSTANCE_NAME'])
	output = poll_output(-1)
	creating_server = False
	if output == "No server with a name or ID of '%s' exists." % deploy_config['INSTANCE_NAME']:
		creating_server = True
		print output
		cmd = 'openstack server create --flavor %s --image %s --nic net-id=%s --security-group %s --key-name %s %s' % (deploy_config['FLAVOR_NAME'],
			deploy_config['IMAGE_NAME'], net_id, deploy_config['SECURITY_GROUP_NAME'],
			deploy_config['KEY_NAME'], deploy_config['INSTANCE_NAME'])
		print "Creating server w/ command:"
		print cmd
		give_command(cmd)
		print poll_output(-1)
		#print poll_all_outputs(init_wait=10000) #My addition
	elif output == "More than one server exists with the name '%s'" % deploy_config['INSTANCE_NAME']:
		print "[WARNING] %s" % output
	else:
		# server exists: don't re-create
		print "Using exisiting server %s" % deploy_config['INSTANCE_NAME']

	if creating_server:
		#Creating floating ip
		utils.print_pass("Upperbound wait of 20 seconds...")
		timebound = 20
		#time.sleep(20) # added to give time for the spawning to complete
		should_proceed = True
		start_time = time.time()
		while True:
			# time bound
			time_elapsed = time.time() - start_time
			if time_elapsed >= timebound:
				utils.print_warning("Giving up on waiting for instance '%s' to become active..." % deploy_config['INSTANCE_NAME'])
				break

			# check server status
			print "Checking status for server '%s'..." % deploy_config['INSTANCE_NAME']
			give_command('openstack server show %s' % deploy_config['INSTANCE_NAME'])
			output = poll_output(-1)
			table = utils.parse_openstack_table(output)
			if table != None:
				row = [entry for entry in table if entry['Field'] == 'status'][0]
				print "Status: %s" % row['Value']
				if row['Value'] == 'ACTIVE':
					utils.print_pass("Server '%s' successfully created!" % deploy_config['INSTANCE_NAME'])
					break
				elif row['Value'] == 'ERROR':
					should_proceed = False
					utils.print_error("Server '%s' is in ERROR state!" % deploy_config['INSTANCE_NAME'])
					break

			time.sleep(1)

		if should_proceed:
			give_command('openstack floating ip create %s' % deploy_config['PUBLIC_NETWORK_NAME'])
			output = poll_output(-1)
			print output
			#utils.print_pass("30-second wait...")
			time.sleep(1)

			table = utils.parse_openstack_table(output)
			ip = [entry['Value'] for entry in table if entry['Field'] == 'floating_ip_address'][0]
			give_command('openstack server add floating ip %s %s' % (deploy_config['INSTANCE_NAME'], ip))
			#print poll_output()
			time.sleep(5)
			utils.print_pass("Upperbound wait of 5 minutes...")
			# time.sleep(120)
			timebound = 300
			start_time = time.time()
			while True:
				# time bound
				time_elapsed = time.time() - start_time
				if time_elapsed >= timebound:
					utils.print_warning("Instance '%s' may have failed to be associated with a floating ip!" % deploy_config['INSTANCE_NAME'])
					break

				# ping
				give_command('ping -w 15 -c 4 %s' % ip)
				output = poll_output(-1)
				lines = output.split('\n')
				if len(lines) > 0:
					# print the first line of ping output (not the title)
					print lines[min(1, len(lines) - 1)]
				rc = get_returncode()
				# utils.print_highlight("Return code: %s" % rc)
				if rc == 0:
					break
				elif type(rc) == int and rc != 0:
					pass
				if type(rc) == str:
					# further ping message...
					print rc
					output = poll_all_outputs()
					print output
					rc = get_returncode()
					# utils.print_highlight("Return code: %s" % rc)
					if rc == 0:
						break

				time.sleep(1)

			# to prevent unsuccessful scp
			for i in xrange(3):
				# for responsiveness sake
				print "Waiting for connection..."
				time.sleep(10)


##########################
## console debug & demo ##
##########################

def demo_parse_table(args=[]):
	print "Command: openstack flavor list"
	print "Waiting for response..."
	give_command('openstack flavor list')
	output = os.read(fromshell_read, BUF_SIZE)
	print "Received output:"
	print output
	print "Parsed object:"
	print utils.parse_openstack_table(output)

##########
## main ##
##########

if __name__ == '__main__':
	main()
