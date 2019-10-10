# vcli
A remote command line interface for [HashiCorp's Vault](https://www.vaultproject.io/) written in bash using as many builtin commands as possible.
Currently only supporting api version v1 and tested on vault 0.9.x

# Why vcli
**vcli**'s purpose is to ease the management of secrets when developing applications that make use of Vault.
As every bash script that's grown beyond it's purpose, **vcli** started with a simple 'for' statement to ease the browse of a [HashiCorp's Vault](https://www.vaultproject.io/) server. It continued as a challenge of what can be achieved simply using bash with its 4.x version powerful features.
Now it has become a useful tool here at [Stratio](http://www.stratio.com)

# Features
* shows Vault's HTTP status code after each command
* persist history on ~/.vclistory
* Ctrl+r: history search
* up & down arrows can be used to select commands from the history
* !$: special variable to access last valid command's path
* tree command implementation
* commands & paths completion using 'tab' key
* autoexpands shell variables in the JSON to upload when using put command
* enable-debug/disable-debug: commands to enable/disable debug output to /tmp/debug
* if exported, vcli will use $VAULT_HOST, $VAULT_TOKEN and $VAULT_PORT or [$VAULT_ADDR](https://github.com/mlorenzo-stratio/vcli/issues/3) to connect to Vault
* Can be exited with Ctrl+d or with exit command
* Can be run in batch mode:
  ```shell
  cat << V | /stratio/secret-utils/vcli -v vault.service.eos.alpha.hetzner.stratio.com -t <root_token> -n
  put /userland/passwords/test {"user":"myuser","pass":"verysecurepass" }
  cat /userland/passwords/test
  rm /userland/passwords/test
  ls /userland/passwords/
  V
  ```

# Demo
[![asciicast](https://asciinema.org/a/132636.png)](https://asciinema.org/a/132636)

# Known bugs
* If you type really fast enough you may incur a race condition on the loop that manages the keyboard input.
* Home/End keys do not work when ran within a screen shell
