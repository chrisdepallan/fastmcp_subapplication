# from time import sleep
# def oddnumberprinting(num):    
#     for i in range(0,num):
#         print(f"Odd number: {i}")
#         sleep(10)
        
# oddnumberprinting(10)
class S:
    
    def __init__(self,x):
        self.x = x


a=S(10)
b=a
print(a == b)