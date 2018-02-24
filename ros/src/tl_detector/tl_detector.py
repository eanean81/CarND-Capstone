#!/usr/bin/env python
import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped, Pose, TwistStamped
from styx_msgs.msg import TrafficLightArray, TrafficLight
from styx_msgs.msg import Lane
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from light_classification.tl_classifier import TLClassifier
import tf
import cv2
import yaml
import math
import numpy as np

STATE_COUNT_THRESHOLD = 3

class TLDetector(object):
    def __init__(self):
        rospy.init_node('tl_detector')

        #Get the maximum velocity parameter
        self.maximum_velocity = self.kmph2mps(rospy.get_param('~velocity')) # change km/h to m/s

        config_string = rospy.get_param("/traffic_light_config")
        self.config = yaml.load(config_string)
        rospy.loginfo("The config type: " + str(type(self.config)))
        # rospy.loginfo("The config sub type: " + str(type(self.config[0])))
        # rospy.loginfo("The config sub sub type: " + str(type(self.config[0][0])))

        self.pose = None
        self.waypoints = None
        self.camera_image = None
        self.current_pose = None
        self.lights = []

        self.base_waypoints_sub = rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)
        self.current_pose_sub = rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        self.vehicle_traffic_lights_sub = rospy.Subscriber('/vehicle/traffic_lights', TrafficLightArray, self.traffic_cb)
        self.current_velocity = 0
        self.current_angular_velocity = 0
        self.current_velocity_sub = rospy.Subscriber('/current_velocity', TwistStamped, self.current_velocity_function)

        '''
        /vehicle/traffic_lights provides you with the location of the traffic light in 3D map space and
        helps you acquire an accurate ground truth data source for the traffic light
        classifier by sending the current color state of all traffic lights in the
        simulator. When testing on the vehicle, the color state will not be available. You'll need to
        rely on the position of the light and the camera image to predict it.
        '''
        sub6_sub = rospy.Subscriber('/image_color', Image, self.image_cb)



        self.upcoming_red_light_pub = rospy.Publisher('/traffic_waypoint', Int32, queue_size=1)

        self.bridge = CvBridge()
        self.light_classifier = TLClassifier()
        self.listener = tf.TransformListener()

        self.state = TrafficLight.UNKNOWN
        self.last_state = TrafficLight.UNKNOWN
        self.last_wp = -1
        self.state_count = 0

        Unknown_Light = 4
        Green_Light = 2
        Yellow_Light = 1
        Red_Light = 0

        stopping_waypoint_index, traffic_light_value, nearest_light = self.process_traffic_lights()
        #obtain the minimum stopping distance possible given the acceleration, and jerk limits, and slow stop point
        acceleration_limit = 10.0 - 1.0
        slow_stop_point = 3
        min_stop_distance = .2*self.current_velocity + (self.current_velocity*(self.current_velocity-slow_stop_point)/acceleration_limit - acceleration_limit/2.0*((self.current_velocity-slow_stop_point)/acceleration_limit)**2) + (0.5*slow_stop_point**2)
        #obtain the maximum stopping distance by changing the acceleration limit to 5
        acceleration_limit -= 4.0
        max_stop_distance = .2*self.current_velocity + (self.current_velocity*(self.current_velocity-slow_stop_point)/acceleration_limit - acceleration_limit/2.0*((self.current_velocity-slow_stop_point)/acceleration_limit)**2) + (0.5*slow_stop_point**2)
        #If the velocity is less than 2*slow_stop_point and the distance to the light is less than 2*(0.5*slow_stop_point**2) and the light is red
        if (self.current_velocity<=2*slow_stop_point and nearest_light<=slow_stop_point**2 and traffic_light_value==Red_Light):
            None
        #if the distance to the nearest_light is more than the max_stop_distance, ignore it
        elif nearest_light > max_stop_distance:
            stopping_waypoint_index = -1
        # else if the traffic light is Yellow or Red and the distance to the nearest light is more than the min_stop_distance
        elif ((traffic_light_value==Red_Light or traffic_light_value==Yellow_Light) and nearest_light>min_stop_distance):
            None
        #else if the traffic light is unknown, stopping waypoint will be -2, telling whatever previous action to keep proceeding
        elif traffic_light_value==Unknown_Light:
            stopping_waypoint_index = -2
        #else if the traffic light is green, ignore it.
        elif traffic_light_value==Green_Light:
            stopping_waypoint_index = -1
        #publish the stopping waypoint index
        self.upcoming_red_light_pub.publish(stopping_waypoint_index)
        

        rospy.spin()

    def kmph2mps(self, velocity_kmph):
        return (velocity_kmph * 1000.) / (60. * 60.)

    def current_velocity_function(self,msg):
        # obtain current_velocity for yaw controller
        self.current_velocity = (msg.twist.linear.x**2 + msg.twist.linear.y**2 + msg.twist.linear.z**2 * 1.0)**(1.0/2)
        #obtain current_angular_velocity for controller
        self.current_angular_velocity = (msg.twist.angular.x**2 + msg.twist.angular.y**2 + msg.twist.angular.z**2 * 1.0)**(1.0/2)
        pass

    def pose_cb(self, msg):
        #given the current position, find the closest traffic light stop line
        #Transform all traffic light coordinates into the current_position coordinate space
        # create variables obtaining the placement of the vehicle
        cx_position = msg.pose.position.x
        cy_position = msg.pose.position.y
        cz_position = msg.pose.position.z
        cw_position = msg.pose.orientation.w
        self.current_pose = msg
        # #Each time the position is changed, determine which traffic light is closest
        # #and which waypoint is closest to that traffic light
        # traffic_light_distances = []
        # waypoint_distances = []
        # #Transform all traffic light coordinates into the current_position coordinate space
        # # create variables obtaining the placement of the vehicle
        # cx_position = msg.pose.orientation.x
        # cy_position = msg.pose.orientation.y
        # cz_position = msg.pose.orientation.z
        # cw_position = msg.pose.orientation.w
        # for each_traffic_light in self.vehicle_traffic_lights.lights:
        #     #create variables for the placement of the traffic_light
        #     each_traffic_lightx = each_traffic_light.pose.pose.orientation.x
        #     each_traffic_lighty = each_traffic_light.pose.pose.orientation.y
        #     each_traffic_lightz = each_traffic_light.pose.pose.orientation.z
        #     # transform the waypoint
        #     shift_x = each_traffic_lightx - cx_position
        #     shift_y = each_traffic_lighty - cy_position
        #     each_traffic_lightx = shift_x * math.cos(0-cw_position) - shift_y * math.sin(0-cw_position)
        #     each_traffic_lighty = shift_x * math.sin(0-cw_position) + shift_y * math.cos(0-cw_position)
        #     #append distance if x is positive, otherwise append a large number
        #     if each_traffic_lightx > 0:
        #         traffic_light_distances.append((each_traffic_lightx**2 + each_traffic_lighty**2*1.0)**(1.0/2))
        #     else:
        #         traffic_light_distances.append(100000000)
        # #find the smallest distance to a traffic light
        # nearest_light = np.amin(traffic_light_distances)
        # #Transform all waypoint coordinates into the current position coordinate space
        # for each_waypoint in self.base_waypoints.waypoints:
        #     #create variables for the placement of the waypoint
        #     each_waypointx = each_waypoint.pose.pose.orientation.x
        #     each_waypointy = each_waypoint.pose.pose.orientation.y
        #     each_waypointz = each_waypoint.pose.pose.orientation.z
        #     # transform the waypoint
        #     shift_x = each_waypointx - cx_position
        #     shift_y = each_waypointy - cy_position
        #     each_waypointx = shift_x * math.cos(0-cw_position) - shift_y * math.sin(0-cw_position)
        #     each_waypointy = shift_x * math.sin(0-cw_position) + shift_y * math.cos(0-cw_position)
        #     #append the distance if x is positive and smaller than the nearest light's distance, otherwise append a small number
        #     if (each_waypointx > 0 and each_waypointx < nearest_light):
        #         waypoint_distances.append((each_waypointx**2 + each_waypointy**2*1.0)**(1.0/2))
        #     else:
        #         waypoint_distances.append(-1)
        # #find the index of the largest distanced waypoint (which is the one closest to the nearest light)
        # self.stopping_waypoint_index = np.argmax(waypoint_distances)

    def waypoints_cb(self, msg):
        # #Record the x and y coordinate of each waypoint
        # waypoints = []
        # for each_waypoint in msg.waypoints:
        #     waypoints.append([each_waypoint.pose.pose.position.x, each_waypoint.pose.pose.position.y])
        # self.waypoints = np.array(waypoints)
        self.base_waypoints = msg

    def traffic_cb(self, msg):
        # #Record the x and y coordinate of each traffic_light
        # traffic_light_coords = []
        # for each_traffic_light in msg.lights:
        #     traffic_light_coords.append([each_traffic_light.pose.pose.position.x , each_traffic_light.pose.pose.position.y])
        # self.traffic_light_coords = np.array(traffic_light_coords)
        # pass
        # #For each traffic light, find the indices of the closest waypoints before and after the traffic light.
        # #Save their indices in a list
        # for each_traffic_light in msg.lights:
        #     #obtain the x coordinate of the traffic light
        #     traffic_light_x = [each_traffic_light.pose.pose.position.x , each_traffic_light.pose.pose.position.y]
        #     #transform the coordinate space with respect to the traffic light. Use its closest coordinate waypoint coordinate
        #     #for the appropriate angle.
        #     #find the indices of the waypoints before and after it.
        #     #subtract the traffic light from the waypoints array, so the positive points are ahead and negative behind
        #     self.waypoints_light = self.waypoints - traffic_light_x
        #     index_before = self.waypoints_light[np.where(self.waypoints_light<0)[0]].argmax
        # self.lights = msg.lights
        self.vehicle_traffic_lights = msg

    def image_cb(self, msg):
        """Identifies red lights in the incoming camera image and publishes the index
            of the waypoint closest to the red light's stop line to /traffic_waypoint

        Args:
            msg (Image): image from car-mounted camera

        """
        self.has_image = True
        self.camera_image = msg
        light_wp, state, nd = self.process_traffic_lights()

        '''
        Publish upcoming red lights at camera frequency.
        Each predicted state has to occur `STATE_COUNT_THRESHOLD` number
        of times till we start using it. Otherwise the previous stable state is
        used.
        '''
        if self.state != state:
            self.state_count = 0
            self.state = state
        elif self.state_count >= STATE_COUNT_THRESHOLD:
            self.last_state = self.state
            light_wp = light_wp if state == TrafficLight.RED else -1
            self.last_wp = light_wp
            self.upcoming_red_light_pub.publish(Int32(light_wp))
        else:
            self.upcoming_red_light_pub.publish(Int32(self.last_wp))
        self.state_count += 1
        sub6 = msg

    def get_closest_waypoint(self, pose):
        """Identifies the closest path waypoint to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to

        Returns:
            int: index of the closest waypoint in self.waypoints

        """
        #TODO implement
        return 0

    def get_light_state(self, light):
        """Determines the current color of the traffic light

        Args:
            light (TrafficLight): light to classify

        Returns:
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        if(not self.has_image):
            self.prev_light_loc = None
            return False

        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")

        #Get classification
        return self.light_classifier.get_classification(cv_image)

    def process_traffic_lights(self):
        """Finds closest visible traffic light, if one exists, and determines its
            location and color

        Returns:
            int: index of waypoint closes to the upcoming stop line for a traffic light (-1 if none exists)
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        #Each time the position is changed, determine which traffic light is closest
        #and which waypoint is closest to that traffic light
        traffic_light_distances = []
        waypoint_distances = []
        #Transform all traffic light coordinates into the current_position coordinate space
        # create variables obtaining the placement of the vehicle
        if self.current_pose is None:
            return 0, TrafficLight.UNKNOWN, 1000
        cx_position = self.current_pose.pose.orientation.x
        cy_position = self.current_pose.pose.orientation.y
        cz_position = self.current_pose.pose.orientation.z
        cw_position = self.current_pose.pose.orientation.w
        for each_traffic_light in self.vehicle_traffic_lights.lights:
            #create variables for the placement of the traffic_light
            each_traffic_lightx = each_traffic_light.pose.pose.orientation.x
            each_traffic_lighty = each_traffic_light.pose.pose.orientation.y
            each_traffic_lightz = each_traffic_light.pose.pose.orientation.z
            # transform the waypoint
            shift_x = each_traffic_lightx - cx_position
            shift_y = each_traffic_lighty - cy_position
            each_traffic_lightx = shift_x * math.cos(0-cw_position) - shift_y * math.sin(0-cw_position)
            each_traffic_lighty = shift_x * math.sin(0-cw_position) + shift_y * math.cos(0-cw_position)
            #append distance if x is positive, otherwise append a large number
            if each_traffic_lightx > 0:
                traffic_light_distances.append((each_traffic_lightx**2 + each_traffic_lighty**2*1.0)**(1.0/2))
            else:
                traffic_light_distances.append(100000000)
        #find the smallest distance to a traffic light
        nearest_light = np.amin(traffic_light_distances)
        #Find the ID of the traffic light color
        #print(type(traffic_light_distances))
        if (np.array(traffic_light_distances) == 100000000).all():
            return -1, TrafficLight.UNKNOWN, 1000
        #Transform all waypoint coordinates into the current position coordinate space
        for each_waypoint in self.base_waypoints.waypoints:
            #create variables for the placement of the waypoint
            each_waypointx = each_waypoint.pose.pose.orientation.x
            each_waypointy = each_waypoint.pose.pose.orientation.y
            each_waypointz = each_waypoint.pose.pose.orientation.z
            # transform the waypoint
            shift_x = each_waypointx - cx_position
            shift_y = each_waypointy - cy_position
            each_waypointx = shift_x * math.cos(0-cw_position) - shift_y * math.sin(0-cw_position)
            each_waypointy = shift_x * math.sin(0-cw_position) + shift_y * math.cos(0-cw_position)
            wp_distance = (each_waypointx**2 + each_waypointy**2*1.0)**(1.0/2)
            #append the distance if x is positive and smaller than the nearest light's distance, otherwise append a small number
            if (each_waypointx > 0 and wp_distance < nearest_light):
                waypoint_distances.append(wp_distance)
            else:
                waypoint_distances.append(-1)
        #find the index of the largest distanced waypoint (which is the one closest to the nearest light)
        self.stopping_waypoint_index = np.argmax(waypoint_distances)


        light = None

        # List of positions that correspond to the line to stop in front of for a given intersection
        stop_line_positions = self.config['stop_line_positions']
        if(self.pose):
            car_position = self.get_closest_waypoint(self.pose.pose)

        #TODO find the closest visible traffic light (if one exists)

        if light:
            state = self.get_light_state(light)
            return light_wp, state
        self.waypoints = None
        return self.stopping_waypoint_index, TrafficLight.UNKNOWN, nearest_light

if __name__ == '__main__':
    try:
        TLDetector()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start traffic node.')
