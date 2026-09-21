import java.io.File;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import org.eclipse.epsilon.emc.plainxml.PlainXmlModel;
import org.eclipse.epsilon.eol.execute.context.Variable;
import org.eclipse.epsilon.evl.EvlModule;
import org.eclipse.epsilon.evl.execute.UnsatisfiedConstraint;

/** Experiment-only headless bridge. The external EVL engine decides failures. */
public class EpsilonArtifactRunner {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("rules.evl input.xml crossArtifact");
        EvlModule module = new EvlModule();
        module.parse(new File(args[0]));
        if (!module.getParseProblems().isEmpty()) {
            throw new IllegalArgumentException(module.getParseProblems().toString());
        }
        PlainXmlModel model = new PlainXmlModel();
        model.setName("M");
        model.setFile(new File(args[1]));
        model.setStoredOnDisposal(false);
        model.load();
        module.getContext().getModelRepository().addModel(model);
        module.getContext().getFrameStack().put(
            Variable.createReadOnlyVariable("crossArtifact", Boolean.parseBoolean(args[2])));
        module.execute();
        ArrayList<String> rows = new ArrayList<>();
        for (UnsatisfiedConstraint failure : module.getContext().getUnsatisfiedConstraints()) {
            String message = failure.getConstraint().getName() + "\t" + failure.getMessage();
            rows.add(Base64.getEncoder().encodeToString(message.getBytes("UTF-8")));
        }
        Collections.sort(rows);
        System.out.println("EPSILON_RESULT_V1");
        for (String row : rows) System.out.println(row);
        System.out.println("EPSILON_END " + rows.size());
        module.getContext().getModelRepository().dispose();
    }
}
