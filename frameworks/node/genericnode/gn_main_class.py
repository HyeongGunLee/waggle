from global_imports import *
from gn_buffer_mngr_class import buffer_mngr_class
from gn_sensor_controller_class import sensor_controller_class
from get_node_info import get_node_info
from gn_global_definition_section import get_instance_id, add_to_thread_buffer, buffered_msg, msg_to_nc, msg_from_nc, start_communication_with_nc_event, \
config_file_initialized_event, sensors_info_saved_event, registration_type, no_reply, config_file_name, logger
from config_file_functions import initialize_config_file, ConfigObj

# Config file is not thread safe and is not locked

# For finding mac address a loop starts with eth0 file name and searches till it gets the first valid eth file name  

# main_thread (object of main_class): Spawns other threads and processes any messages intended for itself              
class main_class():
    
        
    ############################################################################## 
    def __init__(self, thread_name, nc_port, sensor_controller, buffer_mngr):
        self.thread_name = thread_name                   # used by logging module for printing messages related to this thread
        self.reg_msg_handler_no = 0
        self.update_handler_no = 1
        self.status_handler_no = 2
        self.input_buffer = Queue.Queue(maxsize=1000)                # stores messages sent by buffer_mngr_class, sensor_controller_class
        self.nc_port  = nc_port                          # port no. of NC to send to the External_communicator_class
        self.sensor_controller = sensor_controller
        self.buffer_mngr = buffer_mngr
        logger.debug("info Thread "+self.thread_name+" Initialized."+ "\n\n")

    
    ##############################################################################
    # Stores the node's sw/hw info in config file
    def store_node_info(self):
        if os.path.exists(config_file_name):
            config = ConfigObj(config_file_name)
            if config["Systems Info"] != {}:                                            
                # gn.cfg is already present
                return
        else:
            initialize_config_file(config_file_name)
            ret_val = get_node_info(config_file_name)
        # Set the event after which sensor_controller can start storing sensors info in this file
        config_file_initialized_event.set()
        logger.debug("Config file intialize event set."+ "\n\n")
        

    ############################################################################## 
    # Prepares registration msg and adds to buffer_mngr_class's buffer for sending it to NC
    def send_GN_registration_request(self):
        reg_payload = RegistrationPayload()
        # This event is set by sensor_controller after it stores the sensors' info in config file
        logger.debug("Waiting for sensors info."+ "\n\n")
        sensors_info_saved_event.wait()
        #Instance data filling goes here
        config = ConfigObj(config_file_name)
        temp_config = {}
        temp_config["Systems Info"] = config["Systems Info"]
        temp_config["Sensors Info"] = config["Sensors Info"]
        reg_payload.sys_info = dict(temp_config)
        reg_payload.instance_id = get_instance_id()
        self.send_to_buffer_mngr(registration_type, no_reply, reg_payload)
    
    
        
    ############################################################################## 
    # Function: Sends "Ready" packet to NC 
    # Returns: None
    def send_ready_notification(self):
        sensors_info_saved_event.wait()
        # Read sensors info
        reg_payload = "I am UP"
        logger.debug("Reg Msg prepared: " + reg_payload+ "\n\n")
        self.send_to_buffer_mngr(registration_type, no_reply, reg_payload)
        
        
    ############################################################################## 
    # Adds msg to the buffer_mngr's buffer
    def send_to_buffer_mngr(self, msg_type, reply_id, msg):
        buff_msg = buffered_msg(msg_to_nc, msg_type, None, reply_id, msg)                   # adds header msg_to_nc in front of the registration message and returns whole message in string form by adding delimiter
        add_to_thread_buffer(self.buffer_mngr.msg_buffer, buff_msg, "Buffer Mngr")                                 # Sends registration msg by adding to the buffer_mngr's buffer
        logger.debug ("Msg sent to buffer_mngr."+ "\n\n")


        
    ############################################################################## 
    # Function: Checks by reading the log file whether registration has been done or not. 
    def check_registration_status(self):
        config = ConfigObj(config_file_name)
        if config["Registered"] == "YES":
            logger.debug("System Info: " + config["Systems Info"]+ "\n\n")
            logger.info("Registration already done."+ "\n\n")
            return True
        logger.info("Registration not done."+ "\n\n")
        return False
    
        
    ############################################################################## 
    def register_gn(self):
        logger.debug("Checking whether registration is done or not."+ "\n\n")
        if self.check_registration_status():
                # Registration already done so just send msg to NC saying that I am up
                self.send_ready_notification()
        else:
                self.send_GN_registration_request()                                             # sends registration message to the NC to become visible in the outer world

            
    ##############################################################################     
    # Runs forever
    def run(self):
        try:
            logger.debug("Starting " + self.thread_name+ "\n\n")
            self.store_node_info()
            # Spawns 2 threads
            self.sensor_controller = sensor_controller_class("sensor_controller")               # handles sensors related messages
            self.buffer_mngr = buffer_mngr_class("buffer_mngr", self.nc_port)       # handles communication of the GN with NC/other GNs
            self.sensor_controller.pass_thread_address(self, self.buffer_mngr)
            self.buffer_mngr.pass_thread_address(self, self.sensor_controller)
            # Starts sensor Thread
            self.sensor_controller.start()
            # Starts communicator Thread
            self.buffer_mngr.start()
            logger.critical("All threads started:"+str('%0.4f' % time.time())+"\n\n")
            self.register_gn()
            # Loops till a message is received in the input buffer or any unacknowledged msg times out/event like "get threads' status" triggers when its expiration_time is reached        
            # TODO: Add the get status msg in intialize_output buffer or create a separate thread for it
            while True:
                # Checks if any unprocessed msg in the input buffer
                if not self.input_buffer.empty():
                    item = self.input_buffer.get()
                    logger.debug("Msg received in buffer:"+str(item)+ "\n\n")
                    internal_msg_header = item.internal_msg_header
                    # internal_msg_header can be:
                    #   1. msg_from_nc
                    if internal_msg_header == msg_from_nc:
                        process_external_msg(item)                                              # processes msgs obtained from NC/GNs
                    else:
                        logger.critical("Unknown Msg Received: Discarding the msg............." + "\n\n")
                    self.input_buffer.task_done()
                else:
                    pass
                time.sleep(0.01)
        except Exception as inst:
            logger.critical("Exception in main_class: " + str(inst)+ "\n\n")
        finally:
            self.sensor_controller.close()
            self.sensor_controller.join(1)
            self.buffer_mngr.close()
            self.buffer_mngr.join(1)
            logger.critical("All child threads exited. Parent Exiting..."+ "\n\n")
       
    
        
    ############################################################################## 
    # Starts processing msgs received from outside world only after registration is successful, else til then ignores the msgs
    def process_external_msg(self, item):
        logger.debug("Processing NC's msg."+ "\n\n")
        msg = item.msg
        if not start_communication_with_nc_event.isSet():
            # all msgs should be processed only after registration ACK is obtained
            if not is_registration_ack(msg):
                add_to_thread_buffer(self.input_buffer, item, "Main Thread")                                                                          # adds to its own buffer as the registration is not yet successful so the msg should not be processed.
                return
        if msg.reply_id != no_reply: 
            logger.critical("Response received: Discarding it..."+ "\n\n")
            
        else:
            # a new command is received     
            self.process_cmd(msg)
    
        
    ############################################################################## 
    # decodes the msg (seq_no, msg_type, cmd, cmd_args) and maps it to the system specific command/action required
    def process_cmd(self, msg):
        logger.debug("Command from NC received."+ "\n\n")
        if msg == "Update":
            #TODO 
            update_cmd_handler()
        
        elif msg == "Status":
            #TODO
            get_status()
            
    
    ##############################################################################        
    def __del__(self):
        print self, 'Main object died'
   