#!/usr/bin/env bash

# Intended for Ubuntu 17.04 (Trusty)

# Update Ubuntu
echo "----- Provision: Updating apt & Upgrading..."
apt-get update
sudo apt-get -y upgrade

# Adjust timezone to be Los Angeles
echo "----- Provision: Changing default time zone..."
ln -sf /usr/share/zoneinfo/America/Los_Angeles /etc/localtime

# Install and setup git on the new box
# Install git and configure your identification in git
echo "----- Provision: Configuring github account..."
sudo apt install -y git
git config --global user.name "starlordphr"
git config --global user.email "prashanthrajput@ucla.edu"

# Add the OAI repository as authorized remote system
echo "----- Provision: Adding the OAI repository as authorized remote system..."
echo -n | openssl s_client -showcerts -connect gitlab.eurecom.fr:443 2>/dev/null | sed -ne '/-BEGIN CERTIFICATE-/,/-END CERTIFICATE-/p' | sudo tee -a /etc/ssl/certs/ca-certificates.crt

# Install USRP drivers
echo "----- Provision: Installing USRP drivers..."
sudo apt-get -y install libboost-all-dev libusb-1.0-0-dev python-mako doxygen python-docutils python-requests cmake build-essential
git clone git://github.com/EttusResearch/uhd.git
cd uhd; mkdir host/build; cd host/build
cmake -DCMAKE_INSTALL_PREFIX=/usr ..
make -j4
sudo make install
sudo ldconfig
sudo /usr/lib/uhd/utils/uhd_images_downloader.py

# Download & Extract Patches
echo "----- Provision: Downloading & Extracting Patches..."
cd /home/vagrant
wget https://open-cells.com/d5138782a8739209ec5760865b1e53b0/opencells-mods-20170710.tgz
tar xf opencells-mods-20170710.tgz

# Download & Compile the eNB on 17.04
echo "----- Provision: Downloading & Compiling eNB..."
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git
cd openairinterface5g
git checkout develop

# Apply downloaded Patch
echo "----- Provision: Patching eNB..."
# IMPORTANT: This patch fails and all the further setups fail as they are interactive
# Unlike apt I cannot pass -y as a flag to hss, mme and spgw 
# TODO: Find a solution to this
git apply /home/vagrant/opencells-mods/eNB.patch
#cp /home/vagrant/opencells-mods/build/tools/build_helper cmake_targets/tools/build_helper

source oaienv
./cmake_targets/build_oai -I
./cmake_targets/build_oai  -w USRP --eNB --UE

# Clone OAI EPC
echo "----- Provision: Cloning OAI EPC..."
cd /home/vagrant
git clone https://gitlab.eurecom.fr/oai/openair-cn.git
cd openair-cn
git checkout develop

# Apply the patch
echo "----- Provision: Patching OAI EPC..."
git apply /home/vagrant/opencells-mods/EPC.patch

# Install third party SW for EPC
echo "----- Provision: Installing third party SW for EPC..."
source oaienv
cd scripts
./build_hss -i  #Semi-Automatic
./build_mme -i  #Semi-Automatic
./build_spgw -i #Semi-Automatic

./build_hss
./build_mme
./build_spgw

#Network Setup remaining as eNB is not working yet
