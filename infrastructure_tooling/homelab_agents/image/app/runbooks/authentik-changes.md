# Authentik admin object changed

Source: `index=k8s k8s.namespace.name=authentik "Created Event"`, actions `model_created`, `model_updated`, `model_deleted` (JSON: `user.username`, `client_ip`, `context.model.app`, `context.model.model_name`, `context.model.name`, `auth_via`).

Known: about ten a month, all by the owner (`bmo`) changing users, groups, applications or outposts in the admin UI. MFA devices updating their last-used time at each login are already left out.

Check: who made the change and from where; what object (`object` is app/model: name). The ones that matter most: a new user, a user added to an admin group or made superuser, a new MFA device (`authentik_stages_authenticator_*` created) on an account, an MFA device deleted, a flow or stage changed (a stage removed from the login flow can skip MFA), a new token or application. A change by `akadmin` or a recovery key, from an unusual source, or straight after an MFA failure or impersonation, is an incident. Then check what that account did next (`action=login`, `authorize_application`).
