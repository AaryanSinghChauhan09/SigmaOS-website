    # SigmaOS-website

    SigmaOS website & docs site generator

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/omacom/omarchy-site
- https://github.com/omacom/omakub-site

    ## Design principles applied
    - **Separation of Concerns**
- **Defensive Programming**
- **Fail-Fast**
- **KISS**
- **Deterministic Testing**

    ## Usage
    Verbs: `build, nav, search-index, serve-plan`

    ```
    $ sigma-docs build docs/ public/
wrote 12 pages to public/
$ sigma-docs nav docs/
<nav class="site"><a href="index.html">Home</a> ...
$ sigma-docs search-index docs/ > public/search-index.json
$ sigma-docs serve-plan public --port 8080
python3 -m http.server 8080 --bind 127.0.0.1 --directory public

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
