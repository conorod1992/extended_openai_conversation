"""Print the admissibility boundary for human review; never approve it in place."""

import json

from tests_stress.generated_valid_states import generate

print(json.dumps(generate(2983703646, heavy=True).reviewed_inventory(), indent=2, sort_keys=True))
