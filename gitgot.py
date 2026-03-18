#!/usr/bin/env python3

import argparse
import bs4
import github
import json
import re
import requests
import sys
import termios
import tty
import ppdeep as ssdeep
import os
import os.path
import urllib.parse


SIMILARITY_THRESHOLD = 65
ACCESS_TOKEN = os.environ.get("GITHUB_ACCESS_TOKEN")
GITHUB_WHITESPACE = "\\.|,|:|;|/|\\\\|`|'|\"|=|\\*|!|\\?" \
                    "|\\#|\\$|\\&|\\+|\\^|\\||\\~|<|>|\\(" \
                    "|\\)|\\{|\\}|\\[|\\]| "


class bcolors:
    """ Thank you Blender scripts :) """
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    CLEAR = '\x1b[2J'


class State:

    def __init__(self,
                 bad_users=None,
                 bad_repos=None,
                 bad_files=None,
                 bad_signatures=None,
                 checks=None,
                 lastInitIndex=0,
                 index=0,
                 totalCount=0,
                 query=None,
                 logfile="",
                 is_gist=False,
                 line_numbers=False,
                 ):
        self.bad_users = bad_users if bad_users is not None else []
        self.bad_repos = bad_repos if bad_repos is not None else []
        self.bad_files = bad_files if bad_files is not None else []
        self.bad_signatures = bad_signatures if bad_signatures is not None else []
        self.checks = checks if checks is not None else []
        self.lastInitIndex = lastInitIndex
        self.index = index
        self.totalCount = totalCount
        self.query = query
        self.logfile = logfile
        self.is_gist = is_gist
        self.line_numbers = line_numbers


def save_state(name, state):
    filename = state.logfile.replace("log", "state")
    if name == "ratelimited":
        filename += ".ratelimited"
    with open(filename, "w") as fd:
        json.dump(state.__dict__, fd)
    print("Saved as [{}]".format(filename))


def regex_search(checks, repo, print_lines):
    output = ""
    lines = repo.decoded_content.splitlines()

    for i in range(len(lines)):
        line = lines[i]
        try:
            line = line.decode('utf-8')
        except AttributeError:
            pass

        for check in checks:
            try:
                (line, inst) = re.subn(
                    check,
                    bcolors.BOLD + bcolors.OKBLUE + r'\1' + bcolors.ENDC,
                    line)
                if inst > 0:
                    # Line number printing support
                    line_str = ""
                    if print_lines:
                        line_str = bcolors.WARNING + str(i+1) + \
                                   ":" + bcolors.ENDC

                    output += line_str + "\t" + line + "\n"
                    print(line_str + "\t" + line)

                    break
            except Exception as e:
                print(
                    bcolors.FAIL + "ERROR: ", e, bcolors.ENDC,
                    bcolors.WARNING, "\nCHECK: ", check, bcolors.ENDC,
                    "\nLINE: ", line)
    print(bcolors.HEADER + "End of Matches" + bcolors.ENDC)
    return output


def should_parse(repo, state, is_gist=False):
    owner_login = repo.owner.login if is_gist else repo.repository.owner.login
    if owner_login in state.bad_users:
        print(bcolors.FAIL + "Failed check: Ignore User" + bcolors.ENDC)
        return False
    if not is_gist and repo.repository.name in state.bad_repos:
        print(bcolors.FAIL + "Failed check: Ignore Repo" + bcolors.ENDC)
        return False
    if not is_gist and repo.name in state.bad_files:
        print(bcolors.FAIL + "Failed check: Ignore File" + bcolors.ENDC)
        return False

    # Fuzzy Hash Comparison
    try:
        candidate_sig = ssdeep.hash(repo.decoded_content)
        for sig in state.bad_signatures:
            similarity = ssdeep.compare(candidate_sig, sig)
            if similarity > SIMILARITY_THRESHOLD:
                print(
                    bcolors.FAIL +
                    "Failed check: Ignore Fuzzy Signature on Contents "
                    "({}% Similarity)".format(similarity) +
                    bcolors.ENDC)
                return False
    except github.GithubException as e:
        print(bcolors.FAIL + "API ERROR: {}".format(e) + bcolors.ENDC)
        return False
    return True


def print_handler(contents):
    try:
        contents = contents.decode('utf-8')
    except AttributeError:
        pass
    finally:
        print(contents)


def get_single_char():
    """Read one character from stdin instantly, without waiting for Enter.

    Falls back to a full line read when stdin is not a TTY (e.g. in pipes or
    automated tests) so that the rest of the tool still works correctly.
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:  # EOF — treat as quit
            sys.exit(0)
        return line.strip()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def input_handler(state, is_gist):
    prompt = bcolors.HEADER + \
        "(Result {}/{})".format(
            state.index +
            1,
            state.totalCount if state.totalCount < 1000 else "1000+") + \
        "=== " + bcolors.ENDC + \
        "Ignore similar [c]ontents" + \
        bcolors.OKGREEN + "/[u]ser"
    prompt += "" if is_gist else \
        bcolors.OKBLUE + "/[r]epo" + \
        bcolors.WARNING + "/[f]ilename"
    prompt += bcolors.HEADER + \
        ", [p]rint contents, [s]ave state, [a]dd to log, " + \
        "search [/(findme)], [b]ack, [q]uit, next [<Enter>]===: " + \
        bcolors.ENDC

    sys.stdout.write(prompt)
    sys.stdout.flush()

    ch = get_single_char()

    if ch == '/':
        # The search command needs a full regex pattern typed by the user.
        # Echo the '/' and let them type the rest of the pattern + Enter.
        sys.stdout.write(ch)
        sys.stdout.flush()
        rest = input('')
        return ch + rest

    # Echo the pressed key and move to a new line for readability.
    sys.stdout.write(ch + '\n')
    sys.stdout.flush()

    # Treat Enter (both \r and \n) as an empty choice → advance to next result.
    if ch in ('\r', '\n'):
        return ''

    return ch


def pagination_hack(repositories, state):
    count = len(repositories.__dict__["_PaginatedListBase__elements"])
    if state.index >= count:
        n_elements = repositories.get_page(state.index//30)
        repositories.__dict__["_PaginatedListBase__elements"] += n_elements
    return repositories


def regex_handler(choice, repo):
    if choice[1] != "(" or choice[-1] != ")":
        print(
            bcolors.FAIL +
            "Regex requires at least one group reference: "
            "e.g., (CaSeSensitive) or ((?i)insensitive)" +
            bcolors.ENDC)
        return ""
    else:
        print(bcolors.HEADER + "Searching: " + choice[1:] + bcolors.ENDC)
        return regex_search([choice[1:]], repo, False)


def ui_loop(repo, log_buf, state, is_gist=False):
    choice = input_handler(state, is_gist)

    if choice == "c":
        state.bad_signatures.append(ssdeep.hash(repo.decoded_content))
        print(bcolors.OKGREEN + "Added to ignore list: similar contents" + bcolors.ENDC)
    elif choice == "u":
        user = repo.owner.login if is_gist else repo.repository.owner.login
        state.bad_users.append(user)
        print(bcolors.OKGREEN + "Added to ignore list: user [{}]".format(user) + bcolors.ENDC)
    elif choice == "r" and not is_gist:
        state.bad_repos.append(repo.repository.name)
        print(bcolors.OKGREEN + "Added to ignore list: repo [{}]".format(repo.repository.name) + bcolors.ENDC)
    elif choice == "f" and not is_gist:
        state.bad_files.append(repo.name)
        print(bcolors.OKGREEN + "Added to ignore list: filename [{}]".format(repo.name) + bcolors.ENDC)
    elif choice == "p":
        print_handler(repo.decoded_content)
        ui_loop(repo, log_buf, state, is_gist)
    elif choice == "s":
        save_state(state.query, state)
        ui_loop(repo, log_buf, state, is_gist)
    elif choice == "a":
        with open(state.logfile, "a") as fd:
            fd.write(log_buf)
        print(bcolors.OKGREEN + "Result added to log [{}]".format(state.logfile) + bcolors.ENDC)
    elif choice.startswith("/"):
        log_buf += regex_handler(choice, repo)
        ui_loop(repo, log_buf, state, is_gist)
    elif choice == "b":
        if state.index - 1 < state.lastInitIndex:
            print(
                bcolors.FAIL +
                "Can't go backwards past restore point "
                "because of rate-limiting/API limitations" +
                bcolors.ENDC)
            ui_loop(repo, log_buf, state, is_gist)
        else:
            state.index -= 2
    elif choice == "q":
        sys.exit(0)


def gist_fetch(query, page_idx, total_items=1000):
    gist_url = "https://gist.github.com/search?utf8=%E2%9C%93&q={}&p={}"
    query = urllib.parse.quote(query)
    gists = []

    try:
        resp = requests.get(gist_url.format(query, page_idx))
        soup = bs4.BeautifulSoup(resp.text, 'html.parser')
        total_items = min(total_items, int(
            [x.text.split()[0] for x in soup.find_all('h3')
                if "gist results" in x.text][0].replace(',', '')))
        gists = [x.get("href") for x in soup.findAll(
                            "a", class_="link-overlay")]
    except IndexError:
        return {"data": None, "total_items": 0}

    return {"data": gists, "total_items": total_items}


def gist_search(g, state):
    gists = []
    if state.index > 0:
        gists = [None] * (state.index//10) * 10
    else:
        gist_data = gist_fetch(state.query, 0)
        gists = gist_data["data"]
        state.totalCount = gist_data["total_items"]

    if state.totalCount == 0:
        print("No results found for query: {}".format(state.query))
    else:
        print(bcolors.CLEAR)

    i = state.index
    stepBack = False
    while i < state.totalCount:
        while True:
            state.index = i

            # Manual gist paginator
            if i >= len(gists):
                new_gists = gist_fetch(state.query, i // 10)["data"]
                if not new_gists:
                    try:
                        print(
                            bcolors.FAIL +
                            "RateLimitException: "
                            "Please wait about 30 seconds before you "
                            "try again, or exit (CTRL-C).\n " +
                            bcolors.ENDC)
                        save_state("ratelimited", state)
                        input("Press enter to try again...")
                        continue
                    except KeyboardInterrupt:
                        sys.exit(1)
                gists.extend(new_gists)

            gist = g.get_gist(gists[i].split("/")[-1])
            gist.decoded_content = "\n".join(
                [gist_file.content for _, gist_file in gist.files.items()])

            log_buf = "https://gist.github.com/" + \
                bcolors.OKGREEN + gist.owner.login + "/" + \
                bcolors.ENDC + \
                gist.id
            print(log_buf)
            log_buf = "\n" + log_buf + "\n"

            if should_parse(gist, state, is_gist=True) or stepBack:
                stepBack = False
                log_buf += regex_search(state.checks, gist, state.line_numbers)
                ui_loop(gist, log_buf, state, is_gist=True)
                if state.index < i:
                    i = state.index
                    stepBack = True
                print(bcolors.CLEAR)
            else:
                print("Skipping...")
            i += 1
            break


def github_search(g, state):
    print("Collecting Github Search API data...")
    try:
        repositories = g.search_code(state.query)

        state.totalCount = repositories.totalCount

        # Hack to backfill PaginatedList with garbage to avoid ratelimiting on
        # restore, library fetches in 30 counts
        repositories.__dict__["_PaginatedListBase__elements"] = [
            None] * (state.index//30) * 30
        state.lastInitIndex = state.index

        print(bcolors.CLEAR)

        i = state.index
        stepBack = False
        while i < state.totalCount:
            while True:
                try:
                    state.index = i

                    # Manually fill Paginator to avoid ratelimiting on restore
                    repositories = pagination_hack(repositories, state)

                    repo = repositories[i]


                    # Setting domain/scheme name for log output
                    scheme = g._Github__requester._Requester__scheme
                    domain = g._Github__requester._Requester__hostname

                    if(domain == "api.github.com"):
                        domain = "github.com"

                    log_buf = scheme + "://" + \
                        domain + "/" + \
                        bcolors.OKGREEN + repo.repository.owner.login + "/" + \
                        bcolors.OKBLUE + repo.repository.name + "/blob" + \
                        bcolors.ENDC + \
                        os.path.dirname(repo.html_url.split('blob')[1]) + \
                        "/" + bcolors.WARNING + repo.name + bcolors.ENDC
                    print(log_buf)
                    log_buf = "\n" + log_buf + "\n"

                    if should_parse(repo, state) or stepBack:
                        stepBack = False
                        log_buf += regex_search(state.checks, repo,
                                                state.line_numbers)
                        ui_loop(repo, log_buf, state)
                        if state.index < i:
                            i = state.index
                            stepBack = True
                        print(bcolors.CLEAR)
                    else:
                        print("Skipping...")
                    i += 1
                    break
                except github.RateLimitExceededException:
                    try:
                        print(
                            bcolors.FAIL +
                            "RateLimitException: "
                            "Please wait about 30 seconds before you "
                            "try again, or exit (CTRL-C).\n " +
                            bcolors.ENDC)
                        save_state("ratelimited", state)
                        input("Press enter to try again...")
                    except KeyboardInterrupt:
                        sys.exit(1)
    except github.RateLimitExceededException:
        print(
            bcolors.FAIL +
            "RateLimitException: "
            "Please wait about 30 seconds before you try again.\n" +
            bcolors.ENDC)
        save_state("ratelimited", state)
        sys.exit(-1)


def regex_validator(args, state):
    with open(args.checks, "r") as fd:
        for line in fd.read().splitlines():
            if line.startswith("#") or len(line) == 0:
                continue
            try:
                re.subn(line, r'\1', "Expression test")
            except re.error as e:
                print(bcolors.FAIL + "Invalid Regular expression:\n\t" + line)
                if "group" in str(e):
                    print(
                        "Ensure expression contains"
                        "a capture group for matches:\n\t" + str(e))
                sys.exit(-1)
            state.checks.append(line)

    split = []
    if not (state.query[0] == "\"" and state.query[-1] == "\""):
        split = re.split(GITHUB_WHITESPACE, state.query)

    for part in [state.query] + split:
        if part and (part == state.query or len(part) > 3):
            escaped_query = re.escape(part) if split else \
                part.replace("\"", "")
            state.checks.append("(?i)(" + escaped_query + ")")
    return state


def main():
    if sys.version_info < (3, 0):
        sys.stdout.write("Sorry, requires Python 3.x, not Python 2.x\n")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="./" + sys.argv[0] + " -q example.com\n" +
        "./" + sys.argv[0] + " -q example.com -f checks/default.list "
        "-o example1.log\n" +
        "./" + sys.argv[0] + " -q example.com -r example.com.state")
    parser.add_argument(
        "-q",
        "--query",
        help="Github Code Query",
        type=str,
        required=True)
    parser.add_argument(
        "--line-numbers",
        help="Print line numbers",
        action="store_true")
    parser.add_argument(
        "--gist",
        help="Search GitHub Gists instead",
        action="store_true",
        required=False)
    parser.add_argument(
        "-f",
        "--checks",
        help="List of RegEx checks (checks/default.list)",
        type=str,
        default=os.path.dirname(os.path.realpath(__file__)) + "/checks/default.list")
    parser.add_argument(
        "-o",
        "--output",
        help="Log name (default: <query>.log)",
        type=str)
    parser.add_argument(
        "-r",
        "--recover",
        help="Name of recovery file",
        type=str)
    parser.add_argument(
        "-u",
        "--url",
        help="URL of self-hosted GitHub instance (e.g., https://git.example.com)",
        type=str)
    args = parser.parse_args()

    state = State()
    state.index = 0

    if not ACCESS_TOKEN:
        print("Github Access token not set")
        sys.exit(1)

    if args.recover:
        with open(args.recover, 'r') as fd:
            state = State(**json.load(fd))

    args.query = args.query.lstrip()

    # Reusing Blacklists on new query
    if state.query != args.query:
        state.query = args.query
        state.index = 0

    state.is_gist = state.is_gist or (args.gist and not state.is_gist)
    state.line_numbers = state.line_numbers or \
        (args.line_numbers and not state.line_numbers)

    if args.output:
        state.logfile = args.output
    else:
        state.logfile = "logs/" + \
            re.sub(r"[,.;@#?!&$/\\'\"]+\ *", "_", args.query)
        state.logfile += "_gist.log" if state.is_gist else ".log"

    # Create default directories if they don't exist
    try:
        os.mkdir("logs")
        os.mkdir("states")
    except FileExistsError:
        pass

    # Load/Validate RegEx Checks
    state = regex_validator(args, state)

    if args.url:
        g = github.Github(base_url=args.url + "/api/v3",
                          auth=github.Auth.Token(ACCESS_TOKEN))
    else:
        g = github.Github(auth=github.Auth.Token(ACCESS_TOKEN))


    if state.is_gist:
        gist_search(g, state)
    else:
        github_search(g, state)


if __name__ == "__main__":
    main()
