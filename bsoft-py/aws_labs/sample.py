import nuke
import role_credentials


creds = role_credentials._child_creds("188289742290")
nuke.nuke("188289742290","lab-user01",creds)