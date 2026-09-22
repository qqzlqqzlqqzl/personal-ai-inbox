"""Turn upstream ReactFlux login into a single-server personal login."""
from pathlib import Path

ROOT = Path("/home/ubuntu/ai-news")
LOGIN = ROOT / "upstream/reactflux/src/pages/Login.jsx"


def once(text, old, new):
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError("Dedicated login patch anchor missing")


def main():
    s = LOGIN.read_text()
    for old, new in [
        ("  Divider,\n", ""),
        ("  Link,\n", ""),
        ('import { IconHome, IconLock, IconUser } from "@arco-design/web-react/icon"',
         'import { IconLock, IconUser } from "@arco-design/web-react/icon"'),
        ('import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router"',
         'import { Navigate, useLocation, useNavigate } from "react-router"'),
    ]:
        s = s.replace(old, new)
    state_variants = [
        '''  const [searchParams] = useSearchParams()
  const [authMethod, setAuthMethod] = useState(() =>
    Object.fromEntries(searchParams).username ? "user" : "token",
  )
  /* token or user */
''',
        '''  const [searchParams] = useSearchParams()
  const [authMethod, setAuthMethod] = useState(() =>
    "user",
  )
  /* token or user */
''',
    ]
    if '  const authMethod = "user"\n' not in s:
        for generic_state in state_variants:
            if generic_state in s:
                s = s.replace(generic_state, '  const authMethod = "user"\n', 1)
                break
        else:
            raise RuntimeError("Generic auth-method state not found")
    s = s.replace("  const redirectedServer = compatibilityErrorFromNavigation?.server\n", "")

    effect_variants = [
        '''  useEffect(() => {
    hideSpinner()
  }, [])

  useEffect(() => {
    const url = new URL(globalThis.location.href)
    const { server, token, username, password } = Object.fromEntries(url.searchParams)
    if (server) {
      loginForm.setFieldsValue({ server, token, username, password })
      loginForm.submit()
    } else if (redirectedServer) {
      loginForm.setFieldsValue({ server: redirectedServer })
    }
  }, [loginForm, polyglot, redirectedServer])
''',
        '''  useEffect(() => {
    hideSpinner()
    loginForm.setFieldsValue({ server: globalThis.location.origin + "/mf" })
  }, [loginForm])

  useEffect(() => {
    const url = new URL(globalThis.location.href)
    const { server, token, username, password } = Object.fromEntries(url.searchParams)
    if (server) {
      loginForm.setFieldsValue({ server, token, username, password })
      loginForm.submit()
    } else if (redirectedServer) {
      loginForm.setFieldsValue({ server: redirectedServer })
    }
  }, [loginForm, polyglot, redirectedServer])
''',
    ]
    dedicated_effect = '''  useEffect(() => {
    hideSpinner()
    loginForm.setFieldsValue({ username: "qqzl" })
  }, [loginForm])
'''
    if dedicated_effect not in s:
        for generic_effects in effect_variants:
            if generic_effects in s:
                s = s.replace(generic_effects, dedicated_effect, 1)
                break
        else:
            raise RuntimeError("Generic login effects not found")
    s = once(
        s,
        'history.replaceState(history.state, "", "/login")',
        'history.replaceState(history.state, "", `${import.meta.env.BASE_URL}login`)',
    )
    s = once(
        s,
        '''            <Typography.Title heading={3}>
              {polyglot.t("login.login_to_your_server")}
            </Typography.Title>
''',
        '''            <Typography.Title heading={3}>个人信息箱</Typography.Title>
            <Typography.Text disabled>私人 AI 信息收集与阅读</Typography.Text>
''',
    )
    s = once(
        s,
        '''                  await handleLogin(loginForm.getFieldsValue())
''',
        '''                  const values = loginForm.getFieldsValue()
                  await handleLogin({
                    server: globalThis.location.origin + "/mf",
                    username: values.username,
                    password: values.password,
                    token: "",
                  })
''',
    )
    server_start = s.find('''              <Form.Item
                field="server"''')
    if server_start >= 0:
        server_end = s.index('''              {authMethod === "token"''', server_start)
        s = s[:server_start] + s[server_end:]

    generic_footer = s.find(
        '''            <Divider>{polyglot.t("login.another_login_method")}</Divider>'''
    )
    if generic_footer >= 0:
        login_form_close = s.index(
            '''          </div>
        </div>
        <div className="background" />''',
            generic_footer,
        )
        closing = '''          </div>
        </div>
        <div className="background" />'''
        s = s[:generic_footer] + closing + s[login_form_close + len(closing):]

    forbidden = ['field="server"', "setAuthMethod(", "useSearchParams()"]
    if any(x in s for x in forbidden):
        raise RuntimeError("Generic server selection remains in dedicated login")
    LOGIN.write_text(s)
    print("Dedicated login applied: fixed /mf, default user qqzl")


if __name__ == "__main__":
    main()
