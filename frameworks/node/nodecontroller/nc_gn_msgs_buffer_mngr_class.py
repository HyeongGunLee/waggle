
from nc_global_definition_section import *

# gn_msgs_buffer_mngr (object of gn_msgs_buffer_mngr_class): Sends the received messages to msg_processor's buffer and messages received from msg_processor to
# the respective socket connected to the specific guest node       
class gn_msgs_buffer_mngr_class(threading.Thread):
   
    logger = set_logging_level('gn_msgs_buffer_mngr_class') #logging.getLogger('gn_msgs_buffer_mngr_class') 
    #logger = logging.getLogger(None)   
    ##############################################################################  
    def __init__(self, thread_name):
        threading.Thread.__init__(self)
        self.thread_name = "Thread_" + thread_name                                            # used by logging module for printing messages related to this thread
        self.daemon = True
        self.msg_buffer = Queue.Queue(maxsize=1000)                                                       # stores all incoming as well as outgoing msgs and also internal msgs
        self.msg_processor = ''                                                               # to save global msg_processor's input_buffer address
        self.sorted_output_msg_buffer = []
        self.reg_msg_handler_no = 0
        self.handler_vector_table = {}  
        self.log_file_name = "NC_msg_log"
        self.last_nc_seq_no = {}
        self.highest_gn_seq_no = {}
        self.lowest_gn_seq_no = {}
        self.initial_session_id = bytearray([255, 255, 255])
        self.initial_session_seq_no = bytearray([255, 255, 255])
        self.seq_no_partition_size = 3 
        self.error_scope = 100
        self.gn_window_size = 1
        self.nc_window_size = 1
        self.registered_nodes = []
        self.last_nc_seq_no['cloud'] = self.initialize_seq_no('cloud')
        self.gn_instid_socket_obj_mapping = {}                                                # Dict maintaining gn_id and socket mapping
        self.logger.debug(self.thread_name+" Initialized."+"\n\n")

       
    ##############################################################################
    def pass_thread_address(self, msg_processor):
        self.msg_processor = msg_processor
        self.logger.debug("Address of msg processor saved."+"\n\n")
   
       
    ##############################################################################
    def run(self):
        try:
            self.logger.debug("Starting " + self.thread_name+"\n\n")
            # Waits on the buffer till a msg is received      
            while True:
                if not self.msg_buffer.empty():
                    item = self.msg_buffer.get()
                    if item.internal_msg_header == msg_send:                                                           # Send the message to internal_communicator's output_buffer to send it to GN
                        if item.msg_type == reply_type or not self.is_output_buffer_full():
                            self.logger.debug("Message to send to GN/Cloud received.:" + "\n\n")
                            encoded_msg = self.gen_msg(item)
                            if encoded_msg:
                                if item.inst_id != 'cloud':
                                    encoded_msg = encoded_msg + terminator
                                    socket_obj = self.get_socket_obj(item.inst_id)
                                    if socket_obj:
                                        socket_obj.push(encoded_msg)                                                  # Pushes the msg to the appropriate internal_communicator object's buffer which looks over the socket associated with the specific GN
                                        self.logger.info("Msg to GN: "+str(item.inst_id)+" Msg sent: "+ encoded_msg + "\n\n" )
                                    else:
                                        self.logger.critical("GN's socket closed. So discarding the msg------------------------------------------------------------."+ "\n\n")
                                else:
                                        self.logger.info("Msg sent to Cloud:" + (encoded_msg)+"\n\n")
                                        self.send_msg_to_cloud(encoded_msg)                                                             # send msg to cloud
                            else:
                                self.logger.critical("Nonetype Msg discarded."+"\n\n")
                        # push msg bacvk in the buffer till the output_buffer is empty
                        else:
                            self.sorted_output_msg_buffer.put(item)
                    # Received message from GN so send it to msg_processor's input_buffer for processing
                    elif item.internal_msg_header == msg_from_gn:                                                                   # msg from cloud/gn received
                        self.logger.debug("  Msg from GN: " + str(item.msg) + "\n\n")
                        decoded_msg = Message.decode(item.msg)
                        if (not self.new_node(decoded_msg.header.instance_id)) | (decoded_msg.header.message_type == registration_type):
                            msg_state = 'correct'
                            if decoded_msg.header.message_type != reply_type:
                                msg_state = self.get_msg_state(decoded_msg.header.instance_id, decoded_msg.header.sequence_id)
                            if msg_state == 'wrong':
                                self.logger.info("OLD MSG DISCARDED.........................................................................................\n\n")
                                continue
                            # don't process the msg just send ack #TODO add the acks in output buffer before sending so that so that you are sure the msg has been processed before, this ack is put once the ack is received from msg_processor thread
                            elif msg_state == 'dup':
                                self.logger.info("DUPLICATE MSG DISCARDED. SENDING ACK........................................................................................\n\n")
                                decoded_msg.payloads = None
                            # save the new seq_no from that GN
                            self.highest_gn_seq_no[decoded_msg.header.instance_id] = decoded_msg.header.sequence_id
                            if item.inst_id in gn_socket_list and (item.inst_id not in self.gn_instid_socket_obj_mapping):
                                self.gn_instid_socket_obj_mapping[decoded_msg.header.instance_id] = item.inst_id
                            if decoded_msg.header.instance_id not in self.last_nc_seq_no:
                                self.last_nc_seq_no[decoded_msg.header.instance_id] = self.initialize_seq_no(decoded_msg.header.instance_id)
                            item = buffered_msg(item.internal_msg_header, decoded_msg.header.message_type, decoded_msg.header.sequence_id, decoded_msg.header.reply_to_id, decoded_msg.payloads, decoded_msg.header.instance_id)
                            add_to_thread_buffer(self.msg_processor.input_buffer, item, 'Msg_Processor')                                             # Sends to the msg_processor's buffer
                        else:
                            self.logger.critical("UNKNOWN GN SO MSG DISCARDED.........................................."+ "\n\n")
                            continue
                            # TODO: If msg is just an ACK then don't forward it, copy this portion from GN's code, take care of extra inst_id with seq_no in unack_msg_info here
                    self.msg_buffer.task_done()
                time.sleep(0.01)
        except Exception as inst:
            self.logger.critical("Exception in gn_msgs_bufr_mngr run: " + str(inst)+ "\n\n")
            self.run()
    
    
    ############################################################################## 
    def is_output_buffer_full(self):
        # here window size can be checked 
        return len(self.sorted_output_msg_buffer) == self.nc_window_size
     
    
    
    ##############################################################################
    def is_wrap_up(self, new_id, old_id):
        new_id = sum(new_id[i] << ((len(new_id)-1-i) * 8) for i in range(len(new_id)))
        old_id = sum(old_id[i] << ((len(old_id)-1-i) * 8) for i in range(len(old_id)))
        return new_id < (old_id - self.error_scope)
        
    
    ##############################################################################
    # session_id (not <) old_session_id-10 so either == or > or wrap up occured so good
    def in_expected_range(self, new_id, old_id):
        # check for session_id very near to 0.0.0.0 because in that case self.error_scope may give wrong results
        # self.error_scope shows the range of session_id whihch may be old and should be discarded
        return (new_id > old_id) or self.is_wrap_up(copy.deepcopy(new_id), copy.deepcopy(old_id))
    
    
    ##############################################################################
    def check_session_seq_no(self, new_session_seq_no, inst_id):
            if (self.increment_byte_seq(self.highest_gn_seq_no[inst_id][self.seq_no_partition_size:]) == new_session_seq_no): # or falls in the GN window call within gn_window
                return 'correct'
            elif (self.highest_gn_seq_no[inst_id][self.seq_no_partition_size:] == new_session_seq_no):
                # first check the msg is actually duplicate by checking whether its present in the output buffer, erroneous case: the msg_processor thread might have crashed before processing the msg
                return 'dup'
            else:
                # window thing may go here
                return 'wrong'
    
    
    ##############################################################################
    # checks whether the session_seq_no is null or not for lock step protocol, can be modified for window thing
    def session_seq_no_within_gn_window(self, session_seq_no):
        return session_seq_no == bytearray([0,0,0])
    
    
    ##############################################################################
    # checks whether new_session_id is valid or not
    def valid_new_session_id(self, old_session_id, new_session_id):
        if old_session_id:
            return self.in_expected_range(new_session_id, old_session_id)
        # if this GN is contacting the NC for the first time then accept any session_id
        return True
    
    
    ##############################################################################
    def get_msg_state(self, inst_id, seq_id):
        ret_val = ''
        old_session_id = self.get_old_session_id(inst_id, "GN Session ID")
        new_session_id = seq_id[:self.seq_no_partition_size]
        # if any saved session id
        # check whether saved one and new one match
        if old_session_id == new_session_id:
            # GN and NC are both up since they last contacted each other
            if inst_id in self.highest_gn_seq_no:
                # check whether session_seq_no is new or old or duplicate
                ret_val = self.check_session_seq_no(seq_id[self.seq_no_partition_size:], inst_id)
            # GN is up but NC went down since they last contacted eachother so no record of session_seq_no found 
            else:
                ret_val = 'correct'
        # check whether new session id falls in the expected range and the new seq_no is [0,0,0]/in the GN window range
        elif self.valid_new_session_id(old_session_id, new_session_id) and self.session_seq_no_within_gn_window(seq_id[self.seq_no_partition_size:]):            # in the window range
            # save new GN session_id 
            self.save_session_id(inst_id, "GN Session ID", new_session_id)
            ret_val = 'correct'
        return ret_val   
            
            
    ##############################################################################  
    def is_socket_available(self, inst_id):
        #self.logger.info("Socket-ID List:" + str(self.gn_instid_socket_obj_mapping)+"\n\n")
        return (inst_id in self.gn_instid_socket_obj_mapping)
   
   
    ##############################################################################  
    def clean_gn_data(self, del_socket):
        try:
            inst_id = ''
            if self.gn_instid_socket_obj_mapping:
                for gn_id, socket in self.gn_instid_socket_obj_mapping.items():
                        if socket == del_socket:
                            inst_id = gn_id
                            break
                if inst_id:
                    del self.gn_instid_socket_obj_mapping[inst_id]
                    self.logger.info("Socket and seq_no entry removed from id_socket mapping.\nSocket-ID Map:\t"+str(self.gn_instid_socket_obj_mapping)+"\nSeq-ID Map:\t"+\
                                    str(self.last_nc_seq_no)+ "\n\n")
        except Exception as inst:
            self.logger.critical("Exception in clean_gn_data: " + str(inst)+"\n\n")
           
   
    ##############################################################################   
    def gen_msg(self, item):
        try:
            self.last_nc_seq_no[item.inst_id] = self.gen_nc_seq_no(item.inst_id)
            if self.last_nc_seq_no[item.inst_id]:
                header = MessageHeader()
                header.message_type = item.msg_type
                header.instance_id = get_instance_id()
                header.sequence_id = self.last_nc_seq_no[item.inst_id]
                header.reply_to_id = item.reply_id
                msg = Message()
                msg.header = header
                for each_msg in item.msg:
                    msg.append(each_msg)
                msg = msg.encode()  
                self.logger.debug("Msg Encoded."+"\n\n")
                return msg
            del self.last_nc_seq_no[item.inst_id]
            return None
        except Exception as inst:
            self.logger.critical("Exception in gen_msg: " + str(inst)+"\n\n")
           
       
    ##############################################################################
    def send_msg_to_cloud(self, encoded_msg):
        try:
            send_msg(encoded_msg)                                                             # send msg to cloud
            self.logger.info('Msg sent to cloud successfully.'+ "\n\n")
        except Exception as inst:
            self.logger.critical("Exception in send_msg_to_cloud: " + str(inst)+ "\n\n")
            self.logger.critical("Retrying after 1 secs."+ "\n\n")
            time.sleep(1)
            self.send_msg_to_cloud(encoded_msg)
   
   
    ##############################################################################
    # Checks whether a GN is new or not by checking entries in registered nodes or reading config file, if entry is presentin config file but not in registered nodes then it adds that node to registered_nodes list
    def new_node(self, inst_id):
        try:
            if inst_id in self.registered_nodes:
                self.logger.debug("Node is already known."+"\n\n")
                return False
            # Check in config file for the inst_id
            config = ConfigObj(config_file_name)
            if inst_id in config['GN Info']:
                self.logger.debug("Node is already known."+"\n\n")
                self.registered_nodes.append(inst_id)
                return False
            self.logger.debug("New node."+"\n\n")
            return True
        except Exception as inst:
            self.logger.critical("Exception in new_node: " + str(inst)+ "\n\n")
           
           
    ##############################################################################
    def save_session_id(self, inst_id, tag_name, session_id):
        config = ConfigObj(self.log_file_name)
        # create an entry for the GN 
        if inst_id not in config:
            config[inst_id] = {}
        config[inst_id][tag_name] = session_id  
        config.write()
        self.logger.debug("For node new session_id written: " + str(config[inst_id][tag_name])+"\n\n")
                
    
    ##############################################################################
    def initialize_seq_no(self, inst_id):
        try:
            self.logger.debug("Initializing seq_no."+"\n\n")
            if self.new_node(inst_id):
                session_id = str(self.initial_session_id)
            else:
                self.logger.debug("Node already present in the records."+"\n\n")
                session_id = self.get_old_session_id(inst_id, "NC Session ID")
            session_id = self.increment_byte_seq(session_id)
            self.save_session_id(inst_id, "NC Session ID", session_id)
            return (session_id + str(self.initial_session_seq_no))
        except Exception as inst:
            self.logger.critical("Exception in init_seq_no: " + str(inst)+ "\n\n")
    
    
    ##############################################################################
    #checks first in self.highest_gn_seq_no, if found then returns that else checks in log file for that inst_id entry if found returns that, else returns None 
    def get_old_session_id(self, inst_id, tag_name):
        if tag_name == 'GN Session ID':
            if inst_id in self.highest_gn_seq_no:
                return (self.highest_gn_seq_no[inst_id])[:self.seq_no_partition_size]
        config = ConfigObj(self.log_file_name)
        if inst_id in config:
            return config[inst_id][tag_name]
        return None
    

    ##############################################################################
    def increment_byte_seq(self, byte_seq):
        byte_seq = bytearray(byte_seq)
        try:
            for indx in range(len(byte_seq)-1, -1, -1):
                if byte_seq[indx] == 255:
                    # Reset
                    byte_seq[indx] = 0
                else:
                    byte_seq[indx] = byte_seq[indx] + 1
                    break
        except Exception as inst:
            self.logger.critical("Exception in increment_byte_seq: " + str(inst)+ "\n\n")
        return str(byte_seq)
   
     
    ##############################################################################
    # Increments the current sequence no which is maintained by NC to interact with each GN
    def gen_nc_seq_no(self, inst_id):
        try:
            if inst_id in self.last_nc_seq_no:
                session_seq_no = self.increment_byte_seq(self.last_nc_seq_no[inst_id][self.seq_no_partition_size:])
                seq_no = self.last_nc_seq_no[inst_id][:self.seq_no_partition_size] + session_seq_no
                self.logger.debug("SEQUENCE NO. generated: " + seq_no + "for Node:"+str(inst_id)+ "\n\n")
                return seq_no
            return None
        except Exception as inst:
            self.logger.critical("Exception in gen_nc_seq_no: " + str(inst)+ "\n\n")
           
       
    ##############################################################################   
    # Returns the internal_communicator object corresponding to the gn represented by gn_id = (gn_ip, gn_port)         
    def get_socket_obj(self, gn_id):
        try:
            self.logger.debug("Socket object corresponding to specific GN retreived."+ "\n\n")
            if gn_id in self.gn_instid_socket_obj_mapping:
                if self.gn_instid_socket_obj_mapping[gn_id] in gn_socket_list:
                    return self.gn_instid_socket_obj_mapping[gn_id]
                else:
                    del self.gn_instid_socket_obj_mapping[gn_id]
            return None
        except Exception as inst:
            self.logger.critical("Exception in get_socket_obj: " + str(inst)+ "\n\n")
           
       
    ##############################################################################
    def __del__(self):
        print self, 'gn_msgs_bufr_mngr object died.'

