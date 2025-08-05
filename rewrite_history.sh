#\!/bin/bash

# Set git config to ensure all commits use the same author
git config user.name "alvidofaisal"
git config user.email "alvidofaisal@gmail.com"

# Create a backup branch before making changes
git branch backup-before-rewrite
echo "Created backup branch 'backup-before-rewrite'"

# Use git filter-branch to rewrite all commits with consistent authorship
git filter-branch --env-filter '
export GIT_AUTHOR_NAME="alvidofaisal"
export GIT_AUTHOR_EMAIL="alvidofaisal@gmail.com"
export GIT_COMMITTER_NAME="alvidofaisal"
export GIT_COMMITTER_EMAIL="alvidofaisal@gmail.com"
' --msg-filter '
# Read the original commit message
original_message=$(cat)

# Define new messages for each commit (based on commit order)
case "$GIT_COMMIT" in
    916e5e3*)
        echo "Initial commit: Add project structure with app, tests, and deployment files"
        ;;
    57d87ca*)
        echo "Fix mermaid diagram syntax in README

Fixed the mermaid diagram syntax error that was preventing proper rendering of the architecture diagram. Removed extra quotes, simplified connection chains, and fixed bidirectional arrow syntax."
        ;;
    dac775d*)
        echo "Fix mermaid diagram syntax again

Fixed the mermaid diagram syntax by:
1. Using proper subgraph naming with IDs
2. Replacing \n with <br> for line breaks
3. Using proper bidirectional arrow syntax
4. Removing extra quotes around subgraph titles"
        ;;
    fe8c28e*)
        echo "Fix minor README formatting issue"
        ;;
    3c806cc*)
        echo "Update README content with additional information"
        ;;
    6b12455*)
        echo "Fix minor typo in README"
        ;;
    *)
        # For any other commits, keep original message if it is descriptive, otherwise make it more descriptive
        if [[ "$original_message" == "fix" ]] || [[ "$original_message" == "Fix" ]]; then
            echo "Fix minor issue"
        else
            echo "$original_message"
        fi
        ;;
esac
' --tag-name-filter cat -- --all

echo "Commit history rewrite completed\!"
echo "Please verify the changes with 'git log --oneline' and"
echo "push with 'git push --force-with-lease' if satisfied"
EOF < /dev/null