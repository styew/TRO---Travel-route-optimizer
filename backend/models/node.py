# Here creat the Object for Graph
# Every Node is a city and Edge the connection between citys for time/prise

class Node:
    def __init__(self, id, name, lat, lon):
        self.id = id
        self.name = name
        self.lat = lat 
        self.lon = lon 
        