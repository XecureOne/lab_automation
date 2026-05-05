import json
import sample

with open("/home/phoenball/leaky/leaky_dep.json", "r") as f:
    template = json.load(f)

# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/rooms/leaky
# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/box/kali-box

STACK_NAME  = "student-01"
PARAMETERS = [
    {"ParameterKey": "ClusterArn",  "ParameterValue": ""},
    {"ParameterKey": "AppTaskDefinitionArn", "ParameterValue": ""},
    {"ParameterKey": "App2TaskDefinitionArn",  "ParameterValue": "0"}
]



if __name__ == "__main__":
    for i in PARAMETERS:
        value = input(f"Enter the parameter value for {i["ParameterKey"]}:")
        i["ParameterValue"] = value
    print(PARAMETERS)
    create_stack(STACK_NAME,PARAMETERS,json.loads(template))