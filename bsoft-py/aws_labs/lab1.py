client = ''

def test_case1():
    try:
        print("TestCase 01 : Checking iam user with name sample_iam_user")
        try:
            client.get_user(UserName="sample_iam_user")
            print("Status :: Success")
            return True
        except client.exceptions.NoSuchEntityException as e:
            print("Status :: Failure")
            return False
    except Exception as e:
        print(e)

def test_case2():
    try:
        print("TestCase 02 : Checking iam user polices")
        try:
            client.list_user_policies(UserName='sample_iam_user')
            print("Status :: Success")
            return True
        except client.exceptions.NoSuchEntityException as e:
            print("Status :: Failure")
            return False
    except Exception as e:
        print(e)

def run_test_cases(cl):
    global client
    client = cl
    flag = []
    for i in range(1,3):
        flag.append(globals()[f"test_case{i}"]())
    return "Failure" if False in flag else "Success"